import chess

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0
}

def calculate_material(board: chess.Board, color: chess.Color) -> int:
    """Calculates the total traditional heuristic material value for a given color."""
    material = 0
    for piece_type in PIECE_VALUES:
        material += len(board.pieces(piece_type, color)) * PIECE_VALUES[piece_type]
    return material

def generate_board_facts(fen: str, uci_move: str) -> str:
    """
    Evaluates the board before and after a move to generate deterministic, 
    provable facts about the position.
    """
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci_move)
    except ValueError:
        return "Facts unavailable: Invalid FEN or Move."

    if move not in board.legal_moves:
        return "Facts unavailable: Illegal move for this FEN."

    facts = []
    
    # --- PRE-MOVE TACTICAL CHECKS ---
    if board.is_capture(move):
        if board.is_en_passant(move):
            facts.append("Tactical Event: Executes an en passant capture.")
        else:
            captured_piece = board.piece_at(move.to_square)
            if captured_piece:
                piece_name = chess.piece_name(captured_piece.piece_type).title()
                facts.append(f"Tactical Event: Captures an opponent's {piece_name}.")

    # --- PUSH THE MOVE TO THE STACK ---
    board.push(move)

    # --- POST-MOVE TACTICAL CHECKS ---
    if board.is_checkmate():
        facts.append("Tactical Event: Delivers a forced checkmate.")
    elif board.is_check():
        facts.append("Tactical Event: Delivers a check to the enemy king.")

    # --- POST-MOVE MATERIAL EVALUATION ---
    turn_just_played = not board.turn
    my_material = calculate_material(board, turn_just_played)
    opp_material = calculate_material(board, board.turn)
    
    if my_material > opp_material:
        facts.append(f"Structure: Up by {my_material - opp_material} points of material.")
    elif my_material < opp_material:
        facts.append(f"Structure: Down by {opp_material - my_material} points of material (Sacrifice/Imbalance).")
    else:
        facts.append("Structure: Material is perfectly even.")

    return " | ".join(facts)

# Quick local test block
if __name__ == "__main__":
    # Test: Scholar's Mate execution
    print(generate_board_facts("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4", "f3f7"))