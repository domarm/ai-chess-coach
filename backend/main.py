import asyncio
import json
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import chess
import chess.engine

class EngineCluster:
    def __init__(self):
        self.stockfish = None
        self.lc0 = None
        self.board = chess.Board()
        
        # 1. Get the absolute path of the 'backend' folder
        # /Users/domarm/ai-chess-coach/backend
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        
        # 2. Go up TWO levels to reach /Users/domarm/
        # Then go into /lc0/build/lc0
        self.LC0_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "..", "lc0", "build", "lc0"))
        
        # 3. Weights stay in the backend folder
        self.LC0_WEIGHTS = os.path.join(BASE_DIR, "leela_weights.pb.gz")
        
        # Log it for peace of mind
        print(f"🔍 Looking for Lc0 at: {self.LC0_PATH}")

    async def start(self):
        print("🚀 Awakening Engine Cluster on M3 Max...")
        
        # 1. Initialize Stockfish
        # Note: We removed MultiPV here because python-chess manages it during analysis
        _, sf = await chess.engine.popen_uci("stockfish")
        await sf.configure({"Threads": 12, "Hash": 2048})
        self.stockfish = sf

        # 2. Initialize Lc0 (Strategist)
        if os.path.exists(self.LC0_PATH):
            _, l = await chess.engine.popen_uci(self.LC0_PATH)
            await l.configure({"WeightsFile": self.LC0_WEIGHTS})
            self.lc0 = l
            print("🧠 Leela Chess Zero (Metal) is online.")
        else:
            print(f"⚠️ Lc0 binary not found at {self.LC0_PATH}")

    async def get_analysis(self):
        """
        Orchestrates parallel analysis. 
        MultiPV is handled here, which satisfies python-chess.
        """
        # Tactical check (Stockfish)
        sf_task = self.stockfish.analyse(
            self.board, 
            chess.engine.Limit(time=0.3), 
            multipv=3
        )
        
        # Positional check (Lc0)
        lc0_task = self.lc0.analyse(
            self.board, 
            chess.engine.Limit(nodes=500)
        )
        
        sf_info, lc0_info = await asyncio.gather(sf_task, lc0_task)
        
        return {
            "type": "analysis_update",
            "fen": self.board.fen(),
            "stockfish": [
                {
                    "pv": [m.uci() for m in entry.get("pv", [])], 
                    "score": str(entry["score"].white())
                }
                for entry in sf_info
            ],
            "lc0_suggestion": lc0_info.get("pv", [None])[0].uci() if lc0_info.get("pv") else None
        }

cluster = EngineCluster()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cluster.start()
    yield
    # Graceful cleanup
    if cluster.stockfish: await cluster.stockfish.quit()
    if cluster.lc0: await cluster.lc0.quit()

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
    try:
        # Send the current board state immediately upon connection
        await websocket.send_text(json.dumps({
            "type": "init", 
            "fen": cluster.board.fen()
        }))
        
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "move":
                move_uci = message["move"]
                try:
                    move = chess.Move.from_uci(move_uci)
                    if move in cluster.board.legal_moves:
                        cluster.board.push(move)
                        # Push the deep analysis back to the UI
                        analysis = await cluster.get_analysis()
                        await websocket.send_text(json.dumps(analysis))
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "error", 
                            "msg": "Illegal move"
                        }))
                except Exception as e:
                    await websocket.send_text(json.dumps({
                        "type": "error", 
                        "msg": str(e)
                    }))
                    
    except WebSocketDisconnect:
        print("Client disconnected")