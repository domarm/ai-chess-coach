import json
import os
import sys
import chess

# Add the root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend.heuristics import generate_board_facts

# 1. THE SYSTEM PROMPT (Matches Production)
SYSTEM_PROMPT = """You are an elite Grandmaster Chess Coach.
RULES:
1. DO NOT invent piece positions. Base your analysis STRICTLY on the engine evaluations and PROVEN BOARD FACTS provided.
2. DO NOT narrate past moves. DO NOT write sequences of future moves (e.g. 1...d5 2.cxd6).
3. Keep it to 2-3 concise sentences. Focus on concepts like development, space, king safety, and the proven facts."""

def synthesize_jsonl(json_filepath, output_filepath, target_samples=25000):
    print(f"🔄 Booting Neuro-Symbolic Synthesis Pipeline...")
    print(f"📖 Reading from: {json_filepath}")
    
    if not os.path.exists(json_filepath):
        print(f"❌ Error: Could not find {os.path.basename(json_filepath)}. Please ensure it is in the training/ folder.")
        return

    successful_rows = 0
    failed_rows = 0

    # Load the JSON data
    print("⏳ Loading JSON into memory... (This might take a few seconds)")
    with open(json_filepath, mode='r', encoding='utf-8') as f:
        try:
            raw_data = json.load(f)
        except json.JSONDecodeError:
            # Fallback just in case it's actually a line-delimited JSONL disguised as a .json
            f.seek(0)
            raw_data = [json.loads(line) for line in f]

    print(f"✅ Loaded {len(raw_data)} total games/positions. Beginning synthesis...")

    with open(output_filepath, mode='w', encoding='utf-8') as jsonl_file:
        for item in raw_data:
            if successful_rows >= target_samples:
                break
                
            try:
                # 1. Extract data (Handling potential capitalization differences in Kaggle datasets)
                fen_string = item.get('fen') or item.get('FEN')
                move_string = item.get('move') or item.get('Move')
                human_comment = item.get('comment') or item.get('commentary') or item.get('explanation')

                if not fen_string or not move_string or not human_comment:
                    failed_rows += 1
                    continue

                # 2. Normalize the move format (Kaggle datasets often use SAN, our engine needs UCI)
                board = chess.Board(fen_string)
                turn_str = "White" if board.turn == chess.WHITE else "Black"
                
                try:
                    # Try parsing as SAN first (e.g., "Nf3")
                    move_obj = board.parse_san(move_string)
                except ValueError:
                    # Fallback to UCI (e.g., "g1f3")
                    move_obj = chess.Move.from_uci(move_string)
                
                uci_move = move_obj.uci()

                # 3. Generate the Proven Math Facts!
                facts = generate_board_facts(fen_string, uci_move)
                
                # If the heuristic engine rejected the FEN/Move, skip this row
                if "unavailable" in facts.lower():
                    failed_rows += 1
                    continue

                # 4. Construct the production User Prompt
                user_prompt = (
                    f"Current Turn: It is {turn_str}'s turn.\n\n"
                    f"Stockfish (Tactical) prefers: {move_string}\n\n"
                    f"PROVEN BOARD FACTS (Use this to guide your analysis!): {facts}\n\n"
                    f"Based on this data and the provided heuristics, provide concise, expert commentary."
                )
                
                # 5. Format as ChatML JSON
                conversation = {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": human_comment.strip()} 
                    ]
                }
                
                # 6. Write to disk
                jsonl_file.write(json.dumps(conversation) + "\n")
                successful_rows += 1
                
                if successful_rows % 5000 == 0:
                    print(f"🎯 Synthesized {successful_rows} / {target_samples} high-quality rows...")

            except Exception as e:
                # Silently catch corrupted FENs or weird data and keep moving
                failed_rows += 1
                continue

    print("\n✅ SYNTHESIS COMPLETE")
    print(f"🏁 Target reached: {successful_rows} examples successfully processed.")
    print(f"🗑️ Skipped corrupted/invalid rows: {failed_rows}")
    print(f"💾 File saved to: {output_filepath}")

if __name__ == "__main__":
    JSON_INPUT = os.path.join(os.path.dirname(__file__), "chess_commentary_cleaned_combined.json")
    JSONL_OUTPUT = os.path.join(os.path.dirname(__file__), "train.jsonl")
    
    # We target 25,000 for the perfect M3 Max fine-tuning balance
    synthesize_jsonl(JSON_INPUT, JSONL_OUTPUT, target_samples=25000)