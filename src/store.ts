import { create } from 'zustand';

// 1. LINTER FIX: Define a strict interface to remove the 'any' type
export interface AnalysisPayload {
  fen: string;
  stockfish: { pv: string[]; score: string }[];
  lc0_suggestion: string | null;
}

interface ChessState {
  fen: string;
  stockfishData: { pv: string[]; score: string }[];
  lc0Suggestion: string | null;
  setAnalysis: (data: AnalysisPayload) => void;
  updateFen: (newFen: string) => void;
}

export const useChessStore = create<ChessState>((set) => ({
  fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
  stockfishData: [],
  lc0Suggestion: null,
  setAnalysis: (data) => set({
    fen: data.fen,
    stockfishData: data.stockfish || [],       
    lc0Suggestion: data.lc0_suggestion || null 
  }),
  updateFen: (newFen) => set({ fen: newFen })
}));