import json
import os
import sys
import chess
from datasets import load_dataset

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend.heuristics import generate_board_facts

SYSTEM_PROMPT = """You are an elite Grandmaster Chess Coach.
RULES:
1. DO NOT invent piece positions. Base your analysis STRICTLY on the engine evaluations and PROVEN BOARD FACTS provided.
2. DO NOT narrate past moves. DO NOT write sequences of future moves (e.g. 1...d5 2.cxd6).
3. Keep it to 2-3 concise sentences. Focus on concepts like development, space, king safety, and the proven facts."""

def synthesize_hf_dataset(output_filepath, target_samples=25000):
    print(f"🔄 Booting Neuro-Symbolic HF Pipeline...")
    print(f"📥 Streaming 'aicrowd/ChessExplained' from Hugging Face...")
    
    # We use streaming=True so we don't download 2.5 million rows into RAM
    dataset = load_dataset("aicrowd/ChessExplained", split="train", streaming=True)
    
    successful_rows = 0
    failed_rows = 0

    with open(output_filepath, mode='w', encoding='utf-8') as jsonl_file:
        for i, row in enumerate(dataset):
            if successful_rows >= target_samples:
                break
                
            try:
                # Print the schema on the very first row so we know what we are dealing with!
                if i == 0:
                    print(f"📊 Dataset Schema Detected: {list(row.keys())}")
                
                # Normalize all dictionary keys to lowercase so we don't worry about capitalization
                clean_row = {str(k).lower(): v for k, v in row.items()}
                
                # 1. EXTRACT DATA (Smart Fallbacks)
                # First try to pull by key name. If the key doesn't exist, pull by column index.
                fen_string = clean_row.get('fen') or clean_row.get('board') or str(list(clean_row.values())[0])
                
                move_string = clean_row.get('move') or clean_row.get('uci') or clean_row.get('san') 
                if not move_string and len(clean_row.values()) > 1:
                    move_string = str(list(clean_row.values())[1])
                    
                human_comment = clean_row.get('explanation') or clean_row.get('comment') or clean_row.get('text')
                if not human_comment and len(clean_row.values()) > 2:
                    human_comment = str(list(clean_row.values())[2])

                # Validation
                if not fen_string or not move_string or not human_comment:
                    failed_rows += 1
                    continue

                # 2. Normalize the move format
                board = chess.Board(fen_string)
                turn_str = "White" if board.turn == chess.WHITE else "Black"
                
                try:
                    move_obj = board.parse_san(move_string)
                except ValueError:
                    move_obj = chess.Move.from_uci(move_string)
                
                uci_move = move_obj.uci()

                # 3. Generate the Proven Math Facts!
                facts = generate_board_facts(fen_string, uci_move)
                
                if "unavailable" in facts.lower():
                    failed_rows += 1
                    continue

                # 4. Construct Prompt
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
                
                if successful_rows % 1000 == 0:
                    print(f"🎯 Synthesized {successful_rows} / {target_samples} high-quality rows...")

            except Exception as e:
                failed_rows += 1
                continue

    print("\n✅ SYNTHESIS COMPLETE")
    print(f"🏁 Target reached: {successful_rows} examples successfully processed.")
    print(f"🗑️ Skipped corrupted/invalid rows: {failed_rows}")
    print(f"💾 File saved to: {output_filepath}")

if __name__ == "__main__":
    JSONL_OUTPUT = os.path.join(os.path.dirname(__file__), "train.jsonl")
    synthesize_hf_dataset(JSONL_OUTPUT, target_samples=25000)