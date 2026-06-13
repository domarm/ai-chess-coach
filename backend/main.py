import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
# UPGRADE: Utilize free-threaded sub-interpreter pools for true CPU-bound parallelism
from concurrent.futures import InterpreterPoolExecutor 
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import chess
import chess.engine
import chess.pgn
import io
from openai import AsyncOpenAI
from typing import Optional

llm_client = AsyncOpenAI(
    base_url="http://localhost:8888/v1",
    api_key="not-needed" 
)

def format_response_task(fen_string: str, sf_pvs: list, sf_scores: list, lc0_pv_uci: str | None):
    """
    Top-level, cross-interpreter safe function.
    All inputs are primitive types (strings and lists).
    """
    import chess 
    
    # Reconstruct a lightweight board inside the sub-interpreter to calculate SAN
    temp_board = chess.Board(fen_string)
    
    stockfish_data = []
    for pv_ucis, score_str in zip(sf_pvs, sf_scores):
        san_pv = []
        pv_board = temp_board.copy()
        
        for uci in pv_ucis:
            try:
                move = chess.Move.from_uci(uci)
                san_pv.append(pv_board.san(move))
                pv_board.push(move)
            except Exception:
                san_pv.append(uci)
        
        stockfish_data.append({
            "pv": san_pv,
            "score": score_str
        })
        
    lc0_suggestion = None
    if lc0_pv_uci:
        try:
            move = chess.Move.from_uci(lc0_pv_uci)
            lc0_suggestion = temp_board.san(move)
        except Exception:
            lc0_suggestion = lc0_pv_uci
            
    return {
        "type": "analysis_update",
        "fen": fen_string,
        "stockfish": stockfish_data,
        "lc0_suggestion": lc0_suggestion
    }

class EngineCluster:
    def __init__(self):
        self.stockfish = None
        self.lc0 = None
        self.board = chess.Board()
        self.executor = InterpreterPoolExecutor(max_workers=2) 
        
        # ---> NEW: Thread-safe locks and state tracking
        self.lock = asyncio.Lock()
        self.sf_analysis = None
        self.lc0_analysis = None
        
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.LC0_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "lc0", "build", "lc0"))
        self.LC0_WEIGHTS = os.path.join(BASE_DIR, "leela_weights.pb.gz")

    async def start(self):
        print("🚀 Awakening Engine Cluster on M3 Max (Free-Threaded)...")
        _, sf = await chess.engine.popen_uci("stockfish")
        await sf.configure({"Threads": 12, "Hash": 16384})
        self.stockfish = sf

        if os.path.exists(self.LC0_PATH):
            _, l = await chess.engine.popen_uci(self.LC0_PATH)
            await l.configure({"WeightsFile": self.LC0_WEIGHTS})
            self.lc0 = l
            print("🧠 Leela Chess Zero (Metal) is online.")

    def stop_engines(self):
        """Gracefully sends the 'stop' command to the C++ binaries without killing the Python task."""
        if self.sf_analysis:
            self.sf_analysis.stop()
        if self.lc0_analysis:
            self.lc0_analysis.stop()

    async def get_analysis(self):
        if self.board.is_game_over():
            outcome = self.board.outcome()
            return {
                "type": "game_over",
                "fen": self.board.fen(),
                "termination": outcome.termination.name.lower(),
                "winner": "white" if outcome.winner == chess.WHITE else ("black" if outcome.winner == chess.BLACK else "draw"),
                "stockfish": [],
                "lc0_suggestion": None
            }

        loop = asyncio.get_running_loop()
        
        # 1. Start the engines manually (this does not block)
        self.sf_analysis = await self.stockfish.analysis(self.board, chess.engine.Limit(time=5.0), multipv=3)
        self.lc0_analysis = await self.lc0.analysis(self.board, chess.engine.Limit(time=5.0))
        
        # 2. Wait for engines to finish (either 5s elapses, or stop_engines() is called)
        await self.sf_analysis.wait()
        await self.lc0_analysis.wait()
        
        # 3. Extract the data safely
        sf_info = self.sf_analysis.multipv if self.sf_analysis else []
        lc0_info = self.lc0_analysis.info if self.lc0_analysis else {}
        
        # 4. Extract purely primitive, shareable data
        fen_string = self.board.fen()
        
        sf_pvs = []
        sf_scores = []
        for entry in sf_info:
            sf_pvs.append([m.uci() for m in entry.get("pv", [])])
            sf_scores.append(str(entry["score"].white()) if "score" in entry else "0")
            
        lc0_pv_uci = None
        if lc0_info and lc0_info.get("pv"):
            lc0_pv_uci = lc0_info["pv"][0].uci()
            
        # 5. Fire the primitive data across the boundary
        return await loop.run_in_executor(
            self.executor, 
            format_response_task, 
            fen_string, 
            sf_pvs, 
            sf_scores, 
            lc0_pv_uci
        )

cluster = EngineCluster()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cluster.start()
    yield
    if cluster.stockfish: await cluster.stockfish.quit()
    if cluster.lc0: await cluster.lc0.quit()
    cluster.executor.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- PHASE 3: THE LLM-MODULO VERIFIER & SSE STREAMING ---
class CommentaryRequest(BaseModel):
    fen: str
    pgn: str
    top_move: str
    score: str
    lc0_suggestion: Optional[str] = None
    stockfish_pv: Optional[str] = None

def verify_claims(text: str, fen: str, top_move: str) -> bool:
    """The Hard Critic: Decomposes text and verifies geometric claims against the engine."""
    board = chess.Board(fen)
    # Extract all squares mentioned by the LLM (e.g., e4, f6)
    mentioned_squares = re.findall(r'\b[a-h][1-8]\b', text.lower())
    
    # Extract destination square of the suggested move (e.g., 'f5' from 'Bf5')
    move_dest = re.search(r'[a-h][1-8]', top_move)
    target_sq = move_dest.group(0) if move_dest else ""

    for sq_str in mentioned_squares:
        sq_index = chess.parse_square(sq_str)
        # If the LLM mentions a square that is completely empty AND is not the target of our move...
        if board.piece_at(sq_index) is None and sq_str != target_sq:
            print(f"🛑 Hallucination Blocked: LLM claimed piece on empty square {sq_str}")
            return False # Fails verification
    return True

@app.post("/api/coach/stream")
async def coach_stream(request: CommentaryRequest):
    async def generate_verified_sse():
        board = chess.Board(request.fen)
        turn_color = "White" if board.turn == chess.WHITE else "Black"
        
        # 1. TRANSLATE SAN TO EXPLICIT ENGLISH
        # Instead of "a3", this generates "Pawn from a2 to a3"
        try:
            move_obj = board.parse_san(request.top_move)
            piece = board.piece_at(move_obj.from_square)
            piece_name = chess.piece_name(piece.piece_type).title()
            from_sq = chess.square_name(move_obj.from_square)
            to_sq = chess.square_name(move_obj.to_square)
            explicit_top_move = f"{piece_name} moves from {from_sq} to {to_sq} (Notation: {request.top_move})"
        except Exception:
            explicit_top_move = request.top_move

        # 2. STRICT SYSTEM PROMPT
        system_prompt = """You are an elite Grandmaster Chess Coach.
RULES:
1. DO NOT invent piece positions. Base your analysis STRICTLY on the engine evaluations provided.
2. DO NOT narrate past moves. DO NOT write sequences of future moves (e.g. 1...d5 2.cxd6).
3. Compare the Tactical engine's move with the Positional engine's move.
4. Keep it to 2-3 concise sentences. Focus on concepts like development, space, and king safety."""

        # 3. DUAL-ENGINE USER PROMPT
        # 3. DUAL-ENGINE USER PROMPT WITH PV FUTURE-SIGHT
        lc0_text = f"Leela Chess Zero (Positional) prefers: {request.lc0_suggestion}" if request.lc0_suggestion else "Lc0 data currently unavailable."
        pv_text = f"Stockfish's calculated continuation (PV) is: {request.stockfish_pv}" if request.stockfish_pv else ""
        
        user_prompt = (
            f"Current Turn: It is {turn_color}'s turn.\n\n"
            f"Stockfish (Tactical) prefers: {explicit_top_move} (Eval: {request.score} CP)\n"
            f"{pv_text}\n"
            f"{lc0_text}\n\n"
            f"Based on the Stockfish continuation line, explain the tactical goal of this move. Why does Stockfish prefer it over the Lc0 positional suggestion?"
        )

        try:
            response = await llm_client.chat.completions.create(
                model="lucasdino/Qwen2.5-7B-Chess-BestMoveBestLine-SFT",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=1000, 
                temperature=0.1 
            )
            raw_text = response.choices[0].message.content.strip().strip('"').replace('"', '')
            
            # Programmatic Verification Pass
            if verify_claims(raw_text, request.fen, request.top_move):
                final_text = raw_text
            else:
                # ---> NEW FALLBACK: Guarantees Lc0 is mentioned if hallucination is blocked
                final_text = f"Stockfish prefers the tactical tension of {request.top_move}, calculating the line [{request.stockfish_pv}]. Meanwhile, Lc0 evaluates the structure differently and prefers {request.lc0_suggestion}. Eval rests at {request.score}."

            yield f"data: {json.dumps({'type': 'llm_commentary', 'text': final_text})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'msg': str(e)})}\n\n"

    return StreamingResponse(generate_verified_sse(), media_type="text/event-stream")

# --- WEBSOCKET STRICTLY FOR TACTICAL TELEMETRY ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # ---> NEW: Master state tracker
    move_counter = 0

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            incoming_pgn = message.get("pgn", "")
            
            # Increment the global counter for every new move received
            move_counter += 1
            current_move_id = move_counter
            
            # Instantly tell the engines to stop whatever they are currently calculating
            cluster.stop_engines()

            async def run_and_send_analysis(pgn_string, move_id):
                # Only ONE analysis task is allowed to touch the board and engines at a time
                async with cluster.lock:
                    
                    # If we waited in line for the lock, but the user already made another move, abort!
                    if move_id != move_counter:
                        return
                        
                    try:
                        print(f"⏳ [PIPELINE] Engine analyzing move {move_id}...")
                        
                        cluster.board.reset()
                        game = chess.pgn.read_game(io.StringIO(pgn_string))
                        if game:
                            for move in game.mainline_moves():
                                cluster.board.push(move)
                                
                        analysis = await cluster.get_analysis()
                        
                        # Double-check that no new moves arrived while the 5-second analysis was running
                        if move_id == move_counter:
                            await websocket.send_text(json.dumps(analysis))
                            print(f"✅ [PIPELINE] Analysis complete. Payload transmitted for move {move_id}.")
                        else:
                            print(f"⚠️ [PIPELINE] Analysis for move {move_id} discarded (stale).")
                            
                    except Exception as e:
                        print(f"❌ [PIPELINE] Error: {e}")

            # Fire and forget. The lock and counter will manage the traffic naturally!
            asyncio.create_task(run_and_send_analysis(incoming_pgn, current_move_id))
            
    except Exception as e:
        print(f"Client disconnected or error: {e}")