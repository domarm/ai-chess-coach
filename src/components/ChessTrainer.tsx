import { useState, useEffect, useRef } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';
import { useChessSocket } from '../hooks/useChessSocket';
import { useChessStore } from '../store';

export default function ChessTrainer() {
  const [game, setGame] = useState(new Chess());
  const [commentary, setCommentary] = useState<string>("");
  const [isThinking, setIsThinking] = useState(false);
  
  const { sendMove, isConnected } = useChessSocket();
  
  // ---> RESTORED: The high-frequency engine telemetry is back online
  const backendFen = useChessStore((state) => state.fen);
  const stockfishData = useChessStore((state) => state.stockfishData);
  const lc0Suggestion = useChessStore((state) => state.lc0Suggestion);

  const pgnRef = useRef(game.pgn());
  const pendingMoveRef = useRef<string | null>(null);

  useEffect(() => {
    pgnRef.current = game.pgn();
  }, [game]);

  // ---------------------------------------------------------
  // EFFECT 1: THE SYNCHRONIZER 
  // ---------------------------------------------------------
  useEffect(() => {
    setGame((currentGame) => {
      if (!backendFen) return currentGame;
      
      const localBase = currentGame.fen().split(' ').slice(0, 3).join(' ');
      const backendBase = backendFen.split(' ').slice(0, 3).join(' ');

      if (localBase === backendBase) {
        pendingMoveRef.current = null;
        return currentGame;
      }

      if (pendingMoveRef.current && localBase === pendingMoveRef.current) {
        return currentGame;
      }

      try {
        const newGame = new Chess(backendFen);
        newGame.header('SetUp', '1', 'FEN', backendFen);
        return newGame;
      } catch(e) {
        console.error("Fatal Synchronization Error:", e);
        return currentGame;
      }
    });
  }, [backendFen]);

  // ---------------------------------------------------------
  // EFFECT 2: THE LLM-MODULO VERIFIER (Server-Sent Events)
  // ---------------------------------------------------------
  useEffect(() => {
    if (stockfishData && stockfishData.length > 0) {
      const fetchCommentary = async () => {
        setIsThinking(true);
        setCommentary(""); 
        try {
          const response = await fetch('http://localhost:8000/api/coach/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              fen: backendFen,
              pgn: pgnRef.current, 
              top_move: stockfishData[0].pv[0], 
              score: stockfishData[0].score,
              lc0_suggestion: lc0Suggestion,
              stockfish_pv: stockfishData[0].pv.join(' ')
            })
          });

          const reader = response.body?.getReader();
          const decoder = new TextDecoder();
          
          if (reader) {
            const { value } = await reader.read();
            if (value) {
                const chunk = decoder.decode(value);
                const dataStr = chunk.replace('data: ', '').trim();
                if (dataStr) {
                  const data = JSON.parse(dataStr);
                  if (data.type === 'llm_commentary') setCommentary(data.text);
                }
            }
          }
        } catch (error) {
          console.error("SSE Streaming Connection failed:", error);
        } finally {
          setIsThinking(false);
        }
      };
      fetchCommentary();
    }
  }, [backendFen, stockfishData, lc0Suggestion]); 

  // ---------------------------------------------------------
  // ACTION: EXECUTE MOVE 
  // ---------------------------------------------------------
  function onPieceDrop(sourceSquare: string, targetSquare: string, piece: string) {
    const gameCopy = new Chess();
    gameCopy.loadPgn(game.pgn());

    try {
      const isPromotion =
        (piece[1] === 'P' && piece[0] === 'w' && sourceSquare[1] === '7' && targetSquare[1] === '8') ||
        (piece[1] === 'P' && piece[0] === 'b' && sourceSquare[1] === '2' && targetSquare[1] === '1');

      const moveData: { from: string; to: string; promotion?: string } = { 
        from: sourceSquare, 
        to: targetSquare 
      };
      if (isPromotion) {
        moveData.promotion = 'q';
      }

      const move = gameCopy.move(moveData);
      
      if (move) {
        pendingMoveRef.current = gameCopy.fen().split(' ').slice(0, 3).join(' ');
        setGame(gameCopy);
        
        const uciString = move.lan || `${sourceSquare}${targetSquare}${isPromotion ? 'q' : ''}`;
        sendMove(uciString, gameCopy.pgn());
        return true; 
      }
    } catch (error) {
      console.warn("Invalid piece drop rejected:", error);
      return false;
    }
    
    return false;
  }
// ---------------------------------------------------------
  // PIPELINE TRIPWIRES (Diagnostic Logging)
  // ---------------------------------------------------------
  useEffect(() => {
    if (backendFen) {
      console.log(`[PIPELINE - FRONTEND] Zustand Received FEN:`, backendFen);
      console.log(`[PIPELINE - FRONTEND] Zustand Received Stockfish Data:`, stockfishData);
    }
  }, [backendFen, stockfishData]);
 // ---------------------------------------------------------
  // RENDER UI
  // ---------------------------------------------------------
  return (
    <div className="flex flex-col h-screen bg-black text-white p-6">
      <div className="flex flex-1 gap-8 max-w-7xl mx-auto w-full">
        
        {/* LEFT COLUMN: Board & Move History */}
        <div className="w-[600px] flex-shrink-0 self-start">
          <div className="mb-4 flex items-center justify-between">
            <h1 className="text-2xl font-bold">AI Grandmaster Coach</h1>
            <div className="flex items-center gap-2">
              <span className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`}></span>
              <span className="text-sm font-medium text-gray-300">
                {isConnected ? 'Engine Online' : 'Disconnected'}
              </span>
            </div>
          </div>
          
          <Chessboard 
            position={game.fen()} 
            onPieceDrop={onPieceDrop} 
          />

          {/* ---> Move History UI (Height Increased by 100px) */}
          <div className="mt-6 bg-gray-950 border border-gray-800 rounded-lg p-4 h-[220px] overflow-y-auto flex flex-col">
            <h3 className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 border-b border-gray-800 pb-2">
              Move History
            </h3>
            <p className="text-sm text-gray-300 font-mono leading-relaxed break-words">
              {/* Regex strips all metadata bracket tags like [Event "?"] leaving only the moves */}
              {game.pgn().replace(/\[.*?\]\s*/g, '').trim() || "1. ..."}
            </p>
          </div>
          
        </div>
        
        {/* RIGHT COLUMN: AI Analysis Panels */}
        <div className="flex flex-col flex-1 bg-gray-900 border border-gray-800 rounded-lg overflow-hidden h-[850px]">
          
          {/* ENGINES SECTION: Fixed Height to prevent crushing the Coach */}
          <div className="p-6 overflow-y-auto h-[350px] border-b border-gray-800 flex-shrink-0">
            
            <div className="mb-6">
              <h2 className="text-lg font-bold mb-3 text-blue-400 border-b border-gray-700 pb-2">
                ⚔️ Tactical (Stockfish) - Move {game.fen().split(' ')[5]}
              </h2>
              {stockfishData?.length > 0 ? (
                stockfishData.map((line, i) => (
                  <div key={i} className="mb-2 font-mono text-sm text-gray-300">
                    <span className="text-green-400 font-bold w-12 inline-block">{line.score}</span> 
                    <span>| {game.turn() === 'w' ? `${game.moveNumber()}. ` : `${game.moveNumber()}... `}{line.pv.join(' ')}</span>
                  </div>
                ))
              ) : (
                <p className="text-sm text-gray-500 italic">Awaiting tactical telemetry...</p>
              )}
            </div>

            <div className="mb-6">
              <h2 className="text-lg font-bold mb-3 text-purple-400 border-b border-gray-700 pb-2">
                🧠 Strategy (Lc0) - Move {game.fen().split(' ')[5]}
              </h2>
              {lc0Suggestion ? (
                <div className="font-mono text-sm text-gray-300">
                  <span className="text-purple-400 font-bold">Top Node:</span> {game.turn() === 'w' ? `${game.moveNumber()}. ` : `${game.moveNumber()}... `}{lc0Suggestion}
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">Awaiting positional nodes...</p>
              )}
            </div>

          </div>

          {/* VERIFIED COACH SECTION: Flex-1 forces it to consume all remaining space */}
          <div className="p-6 bg-gray-950 flex flex-col flex-1">
            <h3 className="text-sm font-bold text-gray-500 uppercase tracking-widest mb-4">
              Verified Coach
            </h3>
            
            <div className="flex-1 overflow-y-auto pr-2">
              {isThinking ? (
                 <div className="flex items-center gap-3 text-gray-500 italic text-sm mt-2">
                     <span className="relative flex h-3 w-3">
                       <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                       <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-500"></span>
                     </span>
                     Synthesizing PV sequences...
                 </div>
              ) : (
                 <p className="text-base text-gray-200 leading-relaxed border-l-2 border-blue-500 pl-4 whitespace-pre-wrap">
                   {commentary || "Make a move to begin."}
                 </p>
              )}
            </div>
          </div>
          
        </div>
        
      </div>
    </div>
  );
}