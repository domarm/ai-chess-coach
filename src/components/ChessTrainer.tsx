import { useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';

export default function ChessTrainer() {
  const [game, setGame] = useState(new Chess());
  
  const [moveFrom, setMoveFrom] = useState<string | null>(null);
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const [showPromotionDialog, setShowPromotionDialog] = useState(false);

  function executeMove(source: string, target: string, promotionPiece?: string) {
    const gameCopy = new Chess(game.fen());
    try {
        const move = gameCopy.move({
            from: source,
            to: target,
            promotion: promotionPiece ?? 'q', 
        });

        if (move === null) return false;
        
        setGame(gameCopy);
        return true;
    } catch (error) {
        // We keep warnings so silent failures don't drive us crazy later
        console.warn("Invalid move caught:", error);
        return false;
    }
  }

  function onDrop({ sourceSquare, targetSquare }: { sourceSquare: string; targetSquare: string | null }) {
    if (!targetSquare) return false; 

    const possibleMoves = game.moves({ verbose: true });
    const isPromotion = possibleMoves.some(
        (m) => m.from === sourceSquare && m.to === targetSquare && m.promotion
    );

    if (isPromotion) {
        setMoveFrom(sourceSquare);
        setMoveTo(targetSquare);
        setShowPromotionDialog(true);
        return true; 
    }

    return executeMove(sourceSquare, targetSquare);
  }

  function handleCustomPromotion(piece: string) {
    if (moveFrom && moveTo) {
        executeMove(moveFrom, moveTo, piece);
    }
    
    setMoveFrom(null);
    setMoveTo(null);
    setShowPromotionDialog(false);
  }

  const boardOptions = {
    position: game.fen(),
    onPieceDrop: onDrop,
    boardWidth: 600,
    customDarkSquareStyle: { backgroundColor: '#4b5563' },
    customLightSquareStyle: { backgroundColor: '#e5e7eb' }
  };

  return (
    <div className="flex h-screen w-full bg-gray-900 text-gray-100 font-sans p-6 gap-6">
      
      <div className="flex flex-col flex-1 items-center justify-center">
        <div className="relative w-[600px] h-[600px]">
            <Chessboard options={boardOptions} />

            {showPromotionDialog && (
                <div className="absolute inset-0 z-50 flex items-center justify-center bg-gray-900/70 rounded-lg">
                    <div className="bg-gray-800 p-6 rounded-2xl shadow-2xl border border-gray-600 flex flex-col items-center gap-4">
                        <h3 className="text-white font-bold text-lg">Promote Pawn</h3>
                        <div className="flex gap-4">
                            {['q', 'r', 'b', 'n'].map((p) => (
                                <button
                                    key={p}
                                    onClick={() => handleCustomPromotion(p)}
                                    className="w-16 h-16 bg-gray-700 hover:bg-blue-600 transition-colors rounded-xl flex items-center justify-center text-4xl font-bold text-white shadow-lg"
                                >
                                    {p === 'q' ? '♕' : p === 'r' ? '♖' : p === 'b' ? '♗' : '♘'}
                                </button>
                            ))}
                        </div>
                        <button 
                            onClick={() => {
                                setShowPromotionDialog(false);
                                setMoveFrom(null);
                                setMoveTo(null);
                            }}
                            className="mt-2 text-sm text-gray-400 hover:text-white"
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            )}
        </div>
      </div>

      <div className="flex flex-col w-96 bg-gray-800 border border-gray-700 rounded-lg shadow-xl overflow-hidden">
        <div className="bg-gray-950 p-4 border-b border-gray-700">
            <h2 className="text-xl font-bold text-blue-400">Grandmaster AI</h2>
            <p className="text-xs text-gray-400 mt-1">Status: Disconnected</p>
        </div>
        
        <div className="flex-1 p-4 overflow-y-auto">
            <div className="text-gray-400 italic text-sm">
                System: Engine cluster initializing. Awaiting Phase 2 WebSocket connection...
            </div>
        </div>

        <div className="p-4 bg-gray-900 border-t border-gray-700">
            <input 
                type="text" 
                placeholder="Ask about this position..." 
                disabled
                className="w-full bg-gray-800 text-gray-200 rounded px-4 py-2 border border-gray-600 focus:outline-none focus:border-blue-500 disabled:opacity-50"
            />
        </div>
      </div>

    </div>
  );
}