import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
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
from heuristics import generate_board_facts, project_pv_facts

llm_client = AsyncOpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="mlx-local" 
)

def format_response_task(fen_string: str, sf_pvs_json: str, sf_scores_json: str, lc0_pv_uci: str):
    import chess 
    import json
    from heuristics import generate_board_facts, project_pv_facts 
    
    # 1. Deserialize the safe strings back into Python Lists
    sf_pvs = json.loads(sf_pvs_json)
    sf_scores = json.loads(sf_scores_json)
    
    temp_board = chess.Board(fen_string)
    
    stockfish_data = []
    heuristic_facts = "Facts unavailable."
    
    for i, (pv_ucis, score_str) in enumerate(zip(sf_pvs, sf_scores)):
        san_pv = []
        pv_board = temp_board.copy()
        
        # Calculate heuristics for the TOP engine line
        if i == 0 and len(pv_ucis) > 0:
            base_facts = generate_board_facts(fen_string, pv_ucis[0])
            deep_projection = project_pv_facts(fen_string, pv_ucis)
            heuristic_facts = f"{base_facts}\nDEEP PV PROJECTION: {deep_projection}"
            
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
    if lc0_pv_uci: # Empty string evaluates to False
        try:
            move = chess.Move.from_uci(lc0_pv_uci)
            lc0_suggestion = temp_board.san(move)
        except Exception:
            lc0_suggestion = lc0_pv_uci
            
    # --- DELTA TELEMETRY CALCULATION ---
    delta_msg = "Unknown volatility."
    if len(sf_scores) >= 2:
        try:
            s0 = int(sf_scores[0])
            s1 = int(sf_scores[1])
            delta = abs(s0 - s1)
            
            if delta < 30:
                delta_msg = f"Delta={delta} CP. Quiet position. Multiple viable moves exist. Focus on long-term structure."
            elif delta < 100:
                delta_msg = f"Delta={delta} CP. Moderate tension. This move secures a clear edge over the alternative."
            elif delta < 300:
                delta_msg = f"Delta={delta} CP. Forcing line. The second-best move is significantly worse. Explain why."
            else:
                delta_msg = f"Delta={delta} CP. CRITICAL ONLY MOVE. Alternatives lose instantly or drop massive material."
        except ValueError:
            delta_msg = "Mate sequence detected. Absolute forced tactical line."
            
    return {
        "type": "analysis_update",
        "fen": fen_string,
        "stockfish": stockfish_data,
        "lc0_suggestion": lc0_suggestion,
        "heuristic_facts": heuristic_facts,
        "delta_eval": delta_msg 
    }

class EngineCluster:
    def __init__(self):
        self.stockfish = None
        self.lc0 = None
        self.board = chess.Board()
        self.executor = InterpreterPoolExecutor(max_workers=2) 
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
        
        self.sf_analysis = await self.stockfish.analysis(self.board, chess.engine.Limit(time=5.0), multipv=3)
        self.lc0_analysis = await self.lc0.analysis(self.board, chess.engine.Limit(time=5.0))
        
        await self.sf_analysis.wait()
        await self.lc0_analysis.wait()
        
        sf_info = self.sf_analysis.multipv if self.sf_analysis else []
        lc0_info = self.lc0_analysis.info if self.lc0_analysis else {}
        
        fen_string = self.board.fen()
        
        sf_pvs = []
        sf_scores = []
        for entry in sf_info:
            sf_pvs.append([m.uci() for m in entry.get("pv", [])])
            sf_scores.append(str(entry["score"].white().score(mate_score=10000)) if "score" in entry else "0")
            
        lc0_pv_uci = "" # <-- Changed from None to an empty string to ensure shareability
        if lc0_info and lc0_info.get("pv"):
            lc0_pv_uci = lc0_info["pv"][0].uci()
            
        # ---> FIX: Serialize Python Lists to Strings to cross the Sub-Interpreter boundary safely
        sf_pvs_json = json.dumps(sf_pvs)
        sf_scores_json = json.dumps(sf_scores)
            
        return await loop.run_in_executor(
            self.executor, 
            format_response_task, 
            fen_string, 
            sf_pvs_json, 
            sf_scores_json, 
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

class CommentaryRequest(BaseModel):
    fen: str
    pgn: str
    top_move: str
    score: str
    lc0_suggestion: Optional[str] = None
    stockfish_pv: Optional[str] = None
    heuristic_facts: Optional[str] = None
    delta_eval: Optional[str] = None

def verify_claims(text: str, fen: str, top_move: str) -> bool:
    board = chess.Board(fen)
    mentioned_squares = re.findall(r'\b[a-h][1-8]\b', text.lower())
    move_dest = re.search(r'[a-h][1-8]', top_move)
    target_sq = move_dest.group(0) if move_dest else ""

    for sq_str in mentioned_squares:
        sq_index = chess.parse_square(sq_str)
        if board.piece_at(sq_index) is None and sq_str != target_sq:
            print(f"🛑 Hallucination Blocked: LLM claimed piece on empty square {sq_str}")
            return False 
    return True

@app.post("/api/coach/stream")
async def coach_stream(request: CommentaryRequest):
    async def generate_verified_sse():
        board = chess.Board(request.fen)
        turn_color = "White" if board.turn == chess.WHITE else "Black"
        
        try:
            move_obj = board.parse_san(request.top_move)
            piece = board.piece_at(move_obj.from_square)
            piece_name = chess.piece_name(piece.piece_type).title() if piece else "Piece"
            from_sq = chess.square_name(move_obj.from_square)
            to_sq = chess.square_name(move_obj.to_square)
            explicit_top_move = f"{piece_name} moves from {from_sq} to {to_sq} (Notation: {request.top_move})"
        except Exception:
            explicit_top_move = request.top_move

        sf_primary_move = request.stockfish_pv.split()[0] if request.stockfish_pv else ""
        engines_agree = (sf_primary_move == request.lc0_suggestion)

        max_attempts = 2
        
        for attempt in range(max_attempts):
            try:
                # --- STEP 1: THE DRAFT (SYSTEM 1) ---
                yield f"data: {json.dumps({'type': 'status', 'msg': f'[Attempt {attempt+1}/{max_attempts}] 🧠 Drafting internal analysis...'})}\n\n"
                
                draft_prompt = f"""You are the internal reasoning engine for a Grandmaster.
Analyze this position step-by-step using strict logical deduction. Do not worry about formatting; output your raw analytical thoughts.

--- SYSTEM TELEMETRY ---
Turn: It is {turn_color}'s turn.
Evaluation Context: The current Stockfish score is {request.score}. 
CENTIPAWN POLARITY RULE: A positive score (+) means White is winning. A negative score (-) means Black is winning. A negative score for a Black move is a BRILLIANT outcome. Do not equate a negative score with a "bad move" if it is Black's turn. A score of 0.00 means equality.
Volatility: {request.delta_eval}
Engine Move: {explicit_top_move}
Stockfish PV: {request.stockfish_pv}
Lc0 Suggestion: {request.lc0_suggestion}

--- PROVEN BOARD FACTS ---
{request.heuristic_facts}

--- REQUIRED INTERNAL MONOLOGUE FORMAT ---
You MUST structure your thoughts following these exact steps:
1. PV Trace: Explicitly write out the forcing sequence calculated by Stockfish ({request.stockfish_pv}). State what the opponent is forced to do in response.
2. Tactical Mechanism: Cross-reference the PV with the PROVEN BOARD FACTS. Explain exactly which geometric mechanism makes this move work.
3. The "Why": Explain why the opponent cannot defend against this PV sequence. What structural or tactical flaw is being exploited?

Think brutally and analytically. Base everything on the mathematical telemetry."""
                
                draft_response = await llm_client.chat.completions.create(
                    model="mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
                    messages=[{"role": "user", "content": draft_prompt}],
                    max_tokens=500,
                    temperature=0.3 
                )
                draft_text = draft_response.choices[0].message.content

                # --- STEP 2: THE CRITIQUE (SYSTEM 2) ---
                yield f"data: {json.dumps({'type': 'status', 'msg': f'[Attempt {attempt+1}/{max_attempts}] 🔍 Critiquing logic against bitboard math...'})}\n\n"
                
                critique_prompt = f"""Review the following internal chess analysis for depth, causal linkage, and geometric accuracy.

--- DRAFT ANALYSIS TO REVIEW ---
{draft_text}

--- THE MATHEMATICAL REALITY ---
Proven Board Facts: {request.heuristic_facts}
Engine Telemetry Delta: {request.delta_eval}

--- STRICT CRITIQUE DIRECTIVES ---
You must evaluate the draft against these three absolute criteria:
1. Superficiality Check: Did the draft merely narrate the move coordinates (e.g., "The bishop moves to c4 and attacks f7")? If so, REJECT IT.
2. Causal Depth: Does the analysis explain *why* the opponent's counterplay fails based on the Stockfish PV? Did it explain the geometric logic? If not, REJECT IT.
3. Fact Alignment: Did the draft utilize the exact tactical motifs identified in the Proven Board Facts?

Task: Be brutally critical. If the draft fails ANY of the above directives, rewrite it yourself into a mathematically sound, 2-3 sentence Grandmaster explanation focusing strictly on the PV and the structural geometry. Do not use pleasantries. If the original draft is exceptionally deep, perfect, and meets all criteria, output 'APPROVE: ' followed by the draft."""
                
                critique_response = await llm_client.chat.completions.create(
                    model="mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
                    messages=[{"role": "user", "content": critique_prompt}],
                    max_tokens=300,
                    temperature=0.1
                )
                critique_text = critique_response.choices[0].message.content

                # --- STEP 3: THE FINAL POLISH ---
                yield f"data: {json.dumps({'type': 'status', 'msg': f'[Attempt {attempt+1}/{max_attempts}] ✍️ Polishing Grandmaster prose...'})}\n\n"
                
                polish_system = "You are an elite Grandmaster Chess Coach. Output ONLY 2-3 concise sentences of deep positional and tactical commentary. No pleasantries. No intro."
                polish_prompt = f"""Synthesize the final commentary based on this pipeline.

PROVEN FACTS: {request.heuristic_facts}
VOLATILITY: {request.delta_eval}
CORRECTIONS/CRITIQUE: {critique_text}
                
Write a 2-3 sentence explanation of the move {request.top_move}. Do not narrate the coordinates. Explain the underlying mathematical geometry and forcing sequences."""
                
                polish_response = await llm_client.chat.completions.create(
                    model="mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
                    messages=[
                        {"role": "system", "content": polish_system},
                        {"role": "user", "content": polish_prompt}
                    ],
                    max_tokens=150,
                    temperature=0.1
                )
                final_text = polish_response.choices[0].message.content.strip().strip('"').replace('"', '')

                # --- STEP 4: THE NEURO-SYMBOLIC VERIFIER ---
                yield f"data: {json.dumps({'type': 'status', 'msg': f'[Attempt {attempt+1}/{max_attempts}] 🛡️ Running Python safety verifier...'})}\n\n"
                
                if verify_claims(final_text, request.fen, request.top_move):
                    yield f"data: {json.dumps({'type': 'llm_commentary', 'text': final_text})}\n\n"
                    return 
                else:
                    yield f"data: {json.dumps({'type': 'status', 'msg': f'⚠️ Hallucination detected by Python. Rewriting...'})}\n\n"

            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'msg': f'Agentic Loop Error: {str(e)}'})}\n\n"
                return

        fallback_text = f"Both engines universally calculate {request.top_move} as optimal. The deep structural lines are densely tactical." if engines_agree else f"A clash of styles: Stockfish calculates {request.top_move}, while Lc0 evaluates the structure differently and prefers {request.lc0_suggestion}."
        yield f"data: {json.dumps({'type': 'llm_commentary', 'text': fallback_text})}\n\n"

    return StreamingResponse(generate_verified_sse(), media_type="text/event-stream")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    move_counter = 0

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            incoming_pgn = message.get("pgn", "")
            
            move_counter += 1
            current_move_id = move_counter
            cluster.stop_engines()

            async def run_and_send_analysis(pgn_string, move_id):
                async with cluster.lock:
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
                        
                        if move_id == move_counter:
                            await websocket.send_text(json.dumps(analysis))
                            print(f"✅ [PIPELINE] Analysis complete. Payload transmitted for move {move_id}.")
                        else:
                            print(f"⚠️ [PIPELINE] Analysis for move {move_id} discarded (stale).")
                            
                    except Exception as e:
                        print(f"❌ [PIPELINE] Error: {e}")

            asyncio.create_task(run_and_send_analysis(incoming_pgn, current_move_id))
            
    except Exception as e:
        print(f"Client disconnected or error: {e}")