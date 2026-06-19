import chess

# Standard heuristic piece values for relative pin and defender calculations
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
    return sum(len(board.pieces(pt, color)) * val for pt, val in PIECE_VALUES.items())

def detect_center_control(board: chess.Board, move: chess.Move) -> str:
    """Evaluates the change in hypermodern and classical central square control."""
    center_squares = [chess.E4, chess.D4, chess.E5, chess.D5]
    color = board.turn
    
    pre_control = sum(1 for sq in center_squares if board.is_attacked_by(color, sq))
    board.push(move)
    post_control = sum(1 for sq in center_squares if board.is_attacked_by(color, sq))
    board.pop()
    
    if post_control > pre_control:
        return "Positional Metric: This maneuver increases structural dominance over the critical center squares."
    return ""

def detect_discovered_attacks(board: chess.Board, move: chess.Move) -> str:
    """Detects if moving a piece unmasks an attack from a friendly sliding piece."""
    color = board.turn
    enemy_color = not color
    
    sliders = board.pieces(chess.BISHOP, color) | board.pieces(chess.ROOK, color) | board.pieces(chess.QUEEN, color)
    sliders.discard(move.from_square)
    if not sliders:
        return ""
        
    pre_attacks = set()
    for sq in sliders:
        pre_attacks.update(board.attacks(sq))
        
    board.push(move)
    post_attacks = set()
    for sq in sliders:
        post_attacks.update(board.attacks(sq))
        
    new_attacks = post_attacks - pre_attacks
    discovered_victims = []
    
    for sq in new_attacks:
        piece = board.piece_at(sq)
        if piece and piece.color == enemy_color:
            discovered_victims.append(piece.piece_type)
            
    is_check = board.is_check()
    board.pop()
    
    if is_check and discovered_victims and chess.KING in discovered_victims:
        return "Tactical Geometry: Executes a devastating Discovered Check, unmasking a direct attack on the King."
    elif discovered_victims:
        highest_val_piece = max(discovered_victims, key=lambda pt: PIECE_VALUES.get(pt, 0))
        p_name = chess.piece_name(highest_val_piece).title()
        return f"Tactical Geometry: Unmasks a Discovered Attack against an enemy {p_name}."
    return ""

def detect_pins_and_skewers(board: chess.Board, move: chess.Move) -> str:
    """Detects absolute and relative pins created by the move."""
    sim_board = board.copy()
    sim_board.push(move)
    color = not sim_board.turn 
    enemy_color = sim_board.turn
    facts = []
    
    # 1. Absolute Pin Detection
    for sq in chess.SQUARES:
        piece = sim_board.piece_at(sq)
        if piece and piece.color == enemy_color:
            if sim_board.is_pinned(enemy_color, sq):
                facts.append(f"Tactical Geometry: Creates an Absolute Pin on the enemy {chess.piece_name(piece.piece_type).title()}, neutralizing its mobility.")
                
    # 2. Relative Pin Detection via piece-removal simulation
    our_sliders = sim_board.pieces(chess.BISHOP, color) | sim_board.pieces(chess.ROOK, color) | sim_board.pieces(chess.QUEEN, color)
    for enemy_sq in chess.SQUARES:
        enemy_piece = sim_board.piece_at(enemy_sq)
        if enemy_piece and enemy_piece.color == enemy_color and enemy_piece.piece_type != chess.KING:
            sim_board.remove_piece_at(enemy_sq)
            for slider_sq in our_sliders:
                attacks_now = sim_board.attacks(slider_sq)
                for attacked_sq in attacks_now:
                    target_piece = sim_board.piece_at(attacked_sq)
                    if target_piece and target_piece.color == enemy_color:
                        if PIECE_VALUES[target_piece.piece_type] > PIECE_VALUES[enemy_piece.piece_type]:
                            target_name = chess.piece_name(target_piece.piece_type).title()
                            shield_name = chess.piece_name(enemy_piece.piece_type).title()
                            facts.append(f"Tactical Geometry: Establishes a Relative Pin on the {shield_name}, paralyzing it because it must protect the highly valuable {target_name} behind it.")
                            break
            sim_board.set_piece_at(enemy_sq, enemy_piece)
            
    return " | ".join(list(set(facts)))

def detect_removal_of_defender(board: chess.Board, move: chess.Move) -> str:
    """Detects if a capture removes an enemy piece actively defending another unit."""
    if not board.is_capture(move):
        return ""
        
    captured_sq = move.to_square
    captured_piece = board.piece_at(captured_sq)
    if not captured_piece:
        return ""
        
    enemy_color = captured_piece.color
    my_color = board.turn
    
    defended_squares = board.attacks(captured_sq)
    dependent_pieces = []
    for sq in defended_squares:
        piece = board.piece_at(sq)
        if piece and piece.color == enemy_color:
            dependent_pieces.append(sq)
            
    board.push(move)
    facts = []
    for sq in dependent_pieces:
        piece = board.piece_at(sq)
        if not piece: continue
        attackers = board.attackers(my_color, sq)
        defenders = board.attackers(enemy_color, sq)
        if attackers and len(attackers) > len(defenders):
            p_name = chess.piece_name(piece.piece_type).title()
            facts.append(f"Tactical Geometry: Executes a Removal of Defender. The captured unit was vital to the defense of the {p_name}, which is now critically exposed.")
            break
            
    board.pop()
    return " | ".join(facts)

def generate_board_facts(fen: str, uci_move: str) -> str:
    """Master pipeline for evaluating deterministic, provable tactical and positional facts."""
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci_move)
    except ValueError:
        return "Facts unavailable: Invalid FEN or Move."
        
    if move not in board.legal_moves:
        return "Facts unavailable: Illegal move for this FEN."
        
    facts = []
    facts.append(detect_removal_of_defender(board, move))
    facts.append(detect_discovered_attacks(board, move))
    
    if board.is_capture(move) and not board.is_en_passant(move):
        captured_piece = board.piece_at(move.to_square)
        if captured_piece:
            facts.append(f"Tactical Event: Captures an opponent's {chess.piece_name(captured_piece.piece_type).title()}.")
    elif board.is_en_passant(move):
        facts.append("Tactical Event: Executes an en passant pawn capture.")
        
    facts.append(detect_center_control(board, move))
    facts.append(detect_pins_and_skewers(board, move))
    
    board.push(move)
    
    if board.is_checkmate():
        facts.append("Tactical Event: Delivers a forced checkmate.")
    elif board.is_check():
        facts.append("Tactical Event: Delivers a check to the enemy king.")
        
    turn_just_played = not board.turn
    my_material = calculate_material(board, turn_just_played)
    opp_material = calculate_material(board, board.turn)
    
    if my_material > opp_material:
        facts.append(f"Structural Balance: Up by {my_material - opp_material} points of traditional material.")
    elif my_material < opp_material:
        facts.append(f"Structural Balance: Down by {opp_material - my_material} points of material (Sacrifice/Imbalance).")
        
    clean_facts = [f for f in facts if f]
    return " | ".join(clean_facts) if clean_facts else "Quiet positional maneuver focusing on slow structural improvement."

def project_pv_facts(fen: str, pv_ucis: list) -> str:
    """
    Traverses the entire Stockfish Principal Variation (PV) to calculate 
    deep tactical captures and ultimate material swings at the end of the line.
    """
    try:
        board = chess.Board(fen)
    except ValueError:
        return "PV Projection unavailable."
        
    initial_white = calculate_material(board, chess.WHITE)
    initial_black = calculate_material(board, chess.BLACK)
    
    deep_events = []
    
    for i, uci in enumerate(pv_ucis):
        try:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves: 
                break
            
            # Detect captures deep in the engine line
            if board.is_capture(move) and not board.is_en_passant(move):
                captured_piece = board.piece_at(move.to_square)
                if captured_piece:
                    p_name = chess.piece_name(captured_piece.piece_type).title()
                    color_name = "White" if board.turn == chess.WHITE else "Black"
                    san_move = board.san(move)
                    deep_events.append(f"Ply {i+1} ({san_move}): {color_name} captures a {p_name}")
            
            board.push(move)
        except Exception:
            break
            
    final_white = calculate_material(board, chess.WHITE)
    final_black = calculate_material(board, chess.BLACK)
    
    white_net = final_white - initial_white
    black_net = final_black - initial_black
    
    # If nothing crazy happens, keep the prompt clean
    if white_net == 0 and black_net == 0 and not deep_events:
        return "The forced line results in an equal material trade."
        
    # Format the mathematical truth of the sequence
    summary = f"End of PV Material Swing -> White: {white_net:+}, Black: {black_net:+}. "
    if deep_events:
        summary += "Deep Sequence Geometry: " + " -> ".join(deep_events)
        
    return summary