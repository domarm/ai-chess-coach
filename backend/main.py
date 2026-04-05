import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import chess
import chess.engine

# --- Data Models ---
class MoveRequest(BaseModel):
    move: str  # e.g., "e2e4"

class AnalysisLine(BaseModel):
    pv: List[str]
    score: str
    depth: int
    multipv: int

class BoardStatus(BaseModel):
    fen: str
    is_checkmate: bool
    is_draw: bool
    turn: str

# --- Engine Management ---
class EngineCluster:
    def __init__(self):
        self.stockfish: Optional[chess.engine.AsyncUCIEngine] = None
        # We maintain the absolute board state here
        self.board = chess.Board()

    async def start(self):
        print("🚀 Awakening Stockfish 16.1 on M3 Max...")
        transport, engine = await chess.engine.popen_uci("stockfish")
        
        # Hardware Optimization for M3 Max
        # 12 Threads (Efficiency + Performance cores), 4GB Hash for deep lookahead
        await engine.configure({
            "Threads": 12,
            "Hash": 4096,
            "MultiPV": 3
        })
        self.stockfish = engine

    async def stop(self):
        if self.stockfish:
            await self.stockfish.quit()

    async def get_analysis(self) -> List[AnalysisLine]:
        if not self.stockfish:
            return []
        
        # Analyze for 500ms
        info = await self.stockfish.analyse(
            self.board, 
            chess.engine.Limit(time=0.5), 
            multipv=3
        )
        
        return [
            AnalysisLine(
                pv=[m.uci() for m in entry.get("pv", [])],
                score=str(entry["score"].white()),
                depth=entry.get("depth", 0),
                multipv=i + 1
            )
            for i, entry in enumerate(info)
        ]

# --- App Lifecycle ---
cluster = EngineCluster()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cluster.start()
    yield
    await cluster.stop()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Endpoints ---

@app.get("/status", response_model=BoardStatus)
async def get_status():
    return BoardStatus(
        fen=cluster.board.fen(),
        is_checkmate=cluster.board.is_checkmate(),
        is_draw=cluster.board.is_stalemate() or cluster.board.is_insufficient_material(),
        turn="white" if cluster.board.turn == chess.WHITE else "black"
    )

@app.post("/move")
async def make_move(request: MoveRequest):
    """
    Updates the backend board state. 
    This ensures the backend remains the absolute source of truth.
    """
    try:
        move = chess.Move.from_uci(request.move)
        if move in cluster.board.legal_moves:
            cluster.board.push(move)
            # Trigger immediate analysis of the new position
            analysis = await cluster.get_analysis()
            return {
                "fen": cluster.board.fen(),
                "analysis": analysis
            }
        else:
            raise HTTPException(status_code=400, detail="Illegal move")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UCI format")

@app.get("/analyze")
async def analyze():
    analysis = await cluster.get_analysis()
    return {"analysis": analysis}

@app.post("/reset")
async def reset_board():
    cluster.board.reset()
    return {"status": "Board reset", "fen": cluster.board.fen()}
