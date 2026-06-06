import asyncio
import json
import os
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import chess
import chess.engine

class EngineCluster:
    def __init__(self):
        self.stockfish = None
        self.lc0 = None
        self.board = chess.Board()
        
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.gpu_lock = asyncio.Lock()
        
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
        else:
            print(f"⚠️ Lc0 binary not found at {self.LC0_PATH}")

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

        sf_task = self.stockfish.analyse(
            self.board, 
            chess.engine.Limit(time=0.3), 
            multipv=3
        )
        
        async def get_lc0_analysis():
            async with self.gpu_lock:
                # FIX: Strict time limit guarantees the GPU won't hang on complex nodes
                return await self.lc0.analyse(self.board, chess.engine.Limit(time=0.3))
                
        lc0_task = asyncio.create_task(get_lc0_analysis())
        
        sf_info, lc0_info = await asyncio.gather(sf_task, lc0_task)
        
        def format_response():
            stockfish_data = []
            for entry in sf_info:
                temp_board = self.board.copy()
                san_pv = []
                for move in entry.get("pv", []):
                    try:
                        san_pv.append(temp_board.san(move))
                        temp_board.push(move)
                    except Exception:
                        san_pv.append(move.uci())
                
                stockfish_data.append({
                    "pv": san_pv, 
                    "score": str(entry["score"].white()),
                    "wdl": str(entry.get("wdl")) if entry.get("wdl") else None
                })
                
            lc0_suggestion = None
            if lc0_info.get("pv"):
                try:
                    lc0_suggestion = self.board.san(lc0_info["pv"][0])
                except Exception:
                    lc0_suggestion = lc0_info["pv"][0].uci()
            
            return {
                "type": "analysis_update",
                "fen": self.board.fen(),
                "stockfish": stockfish_data,
                "lc0_suggestion": lc0_suggestion
            }
            
        return await loop.run_in_executor(self.executor, format_response)

cluster = EngineCluster()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cluster.start()
    yield
    if cluster.stockfish: await cluster.stockfish.quit()
    if cluster.lc0: await cluster.lc0.quit()
    cluster.executor.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    loop = asyncio.get_running_loop()
    
    # FIX: Track the active analysis task so we can abort it mid-flight
    active_analysis_task = None
    
    async def run_and_send_analysis():
        try:
            analysis = await cluster.get_analysis()
            await websocket.send_text(json.dumps(analysis))
        except asyncio.CancelledError:
            # Task was instantly killed because the user made a new move
            pass
        except Exception as e:
            print(f"Analysis error: {e}")

    try:
        await websocket.send_text(json.dumps({
            "type": "init", 
            "fen": cluster.board.fen()
        }))
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "move":
                
                # 1. ABORT PREVIOUS ANALYSIS INSTANTLY
                if active_analysis_task and not active_analysis_task.done():
                    active_analysis_task.cancel()
                
                move_uci = message["move"]
                
                def apply_move():
                    try:
                        move = chess.Move.from_uci(move_uci)
                        if move in cluster.board.legal_moves:
                            cluster.board.push(move)
                            return True
                        return False
                    except ValueError:
                        return False
                    
                is_legal = await loop.run_in_executor(cluster.executor, apply_move)
                
                if is_legal:
                    # 2. SPAWN NEW ANALYSIS IN THE BACKGROUND
                    # This allows the while loop to instantly listen for the next move
                    active_analysis_task = asyncio.create_task(run_and_send_analysis())
                else:
                    await websocket.send_text(json.dumps({
                        "type": "error", 
                        "msg": "Illegal move"
                    }))
                    
    except WebSocketDisconnect:
        print("Client disconnected")