import { useEffect, useCallback, useRef, useState } from 'react';
import { useChessStore } from '../store';

export function useChessSocket() {
  const socketRef = useRef<WebSocket | null>(null);
  const setAnalysis = useChessStore((state) => state.setAnalysis);
  const updateFen = useChessStore((state) => state.updateFen);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    const socket = new WebSocket('ws://localhost:8000/ws');
    socketRef.current = socket;

    socket.onopen = () => setIsConnected(true);
    socket.onclose = () => setIsConnected(false);

    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'init') {
        updateFen(data.fen);
      } else if (data.type === 'analysis_update') {
        setAnalysis(data);
      }
    };
    
    return () => socket.close();
  }, [setAnalysis, updateFen]);

  // Send exact UCI format (e.g., e2e4) and the PGN history to the backend
  const sendMove = useCallback((moveUci: string, pgn: string) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: 'move', move: moveUci, pgn: pgn }));
    }
  }, []);

  return { sendMove, isConnected };
}