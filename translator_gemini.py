"""This script uses Google's Gemini AI to translate XML files with chunks of text."""

from google import genai  # pip install google-genai
from google.genai import types

import time
from datetime import datetime
from ratelimit import limits, sleep_and_retry
import re
from pathlib import Path
import argparse
from functools import wraps
import random

GEMINI_API_PROJECT_KEY_FILE = "./gemini_key_project_1.txt"
AI_MODEL = "gemini-2.0-flash"

CALLS_PER_MINUTE = 14
PERIOD = 60


# Configure Gemini
# https://ai.google.dev/gemini-api/docs/rate-limits#free-tier
# free tier per project per day:
# Model	RPM	TPM	RPD
# Gemini 2.0 Flash	15	1,000,000	1,500
# Gemini 2.0 Flash-Lite	30	1,000,000	1,500
# Gemini 2.0 Pro Experimental 02-05	2	1,000,000	50
# Gemini 2.0 Flash Thinking Experimental 01-21	10	4,000,000	1,500

# The "gemini-2.0-flash-thinking-exp-01-21" has max_output_tokens=65536

# Re-tries configures
MAX_RETRIES = 8
INITIAL_RETRY_DELAY = 10
MAX_RETRY_DELAY = 1280


def read_gemini_api_key(key_file=GEMINI_API_PROJECT_KEY_FILE):
    # get a (free) API key from here https://aistudio.google.com/apikey
    print(f"Using key from: {key_file}")
    with open(key_file, "r") as file:
        return file.read().strip()


client = genai.Client(api_key=read_gemini_api_key())


# Gemini safety settings
# https://ai.google.dev/gemini-api/docs/safety-settings
GEMINI_SAFE_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_CIVIC_INTEGRITY",
        threshold="OFF",  # Off
    ),
]


def load_sytem_prompt(prompt_file="./prompt_Sinhala_English.md"):
    with open(prompt_file, "r", encoding="utf-8") as file:
        return file.read()


def retry_with_exponential_backoff(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        retry_count = 0
        while retry_count < MAX_RETRIES:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                retry_count += 1
                if retry_count == MAX_RETRIES:
                    print(f"Final attempt failed: {str(e)}")
                    return None

                # Calculate delay with jitter
                delay = min(
                    INITIAL_RETRY_DELAY * (2 ** (retry_count - 1)), MAX_RETRY_DELAY
                )
                jitter = random.uniform(0, 0.1 * delay)  # 10% jitter
                total_delay = delay + jitter

                print(f"Attempt {retry_count} failed: {str(e)}")
                print(f"Retrying in {total_delay:.2f} seconds...")
                time.sleep(total_delay)
        return None

    return wrapper


@sleep_and_retry
@limits(calls=CALLS_PER_MINUTE, period=PERIOD)
@retry_with_exponential_backoff
def gemini_translate(chunk):
    response = client.models.generate_content(
        model=AI_MODEL,
        contents=f"{load_sytem_prompt()}\n{chunk}",
        config=types.GenerateContentConfig(
            # system_instruction=load_sytem_prompt(),
            top_p=0.8,
            # for thinking model, can get code from
            # temperature=0.7,
            # top_p=0.95,
            # top_k=64,
            # max_output_tokens=65536,
            safety_settings=GEMINI_SAFE_SETTINGS,
        ),
    )
    return response.text


def process_xml_file_with_regex(input_file, transno="translated_1"):
    try:
        # Check if file is already translated
        source_path = Path(input_file)
        base_name = source_path.stem

        # Look for existing translations with the same base name and AI model
        existing_translations = list(
            source_path.parent.glob(f"{base_name}_*_{AI_MODEL}.xml")
        )
        if existing_translations:
            print(f"------> SKIPPING {input_file} - THERE IS A translated FILE here:")
            for trans in existing_translations:
                print(f"  {trans.name}")
            return

        with open(input_file, "r", encoding="utf-8") as file:
            content = file.read()

        # Find all chunks using regex
        chunk_pattern = r"<chunk\d+>(.*?)</chunk\d+>"
        chunks = re.finditer(chunk_pattern, content, re.DOTALL)
        chunks_list = list(chunks)
        total_chunks = len(chunks_list)
        print(f"Found {total_chunks} chunks to translate")

        # Create output file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        output_file = source_path.parent / f"{base_name}_{timestamp}_{AI_MODEL}.xml"
        log_file = source_path.parent / f"{base_name}_{timestamp}_{AI_MODEL}.log"

        with open(output_file, "w", encoding="utf-8") as f, open(
            log_file, "w", encoding="utf-8"
        ) as log_f:

            # Write warning info to the translated files
            info_warning = f"""<info>
Translated by {AI_MODEL}
Started at: {timestamp}
**WARNING: THIS IS AN AI-TRANSLATED EXPERIMENT.**

- Please do not blindly trust the LLM output. LLMs can produce errors. If you are uncertain, refer to the original Pāḷi text for verification.
</info>\n\n"""
            f.write(info_warning)
            # Write initial log info
            log_f.write(f"Translation log for {input_file}\n")
            log_f.write(f"Started at: {timestamp}\n")
            log_f.write(f"API Key file: {GEMINI_API_PROJECT_KEY_FILE}\n\n")
            log_f.write(f"Model: {AI_MODEL}\n\n")

            for i, chunk in enumerate(chunks_list, 1):
                input_chunk_text = chunk.group(0)  # Full chunk with tags

                if input_chunk_text.strip():
                    input_chunk_text = input_chunk_text.strip()

                    print(f"\nTranslating chunk {i}/{total_chunks}")

                    start_time = time.time()
                    translated_text = gemini_translate(input_chunk_text)
                    end_time = time.time()
                    elapsed_time = end_time - start_time

                    # Handle None returns from translation
                    if translated_text is None:
                        log_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        error_message = f"Chunk {i}: CHUNK_FAILED at {log_time}. Translation returned None after all retries.\n"
                        print(error_message.strip())
                        log_f.write(error_message)
                        # Write original text instead of translation
                        f.write(input_chunk_text)
                        f.write("\n\n")
                    else:
                        f.write(translated_text)
                        f.write("\n\n")
                        f.flush()

                        log_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        log_message = f"Chunk {i}: is written at {log_time}. Took: {elapsed_time:.2f}s. Translated chars/input chars: {len(translated_text)}/{len(input_chunk_text)}\n"
                        print(log_message.strip())
                        log_f.write(log_message)

                    log_f.flush()

        print(f"Translation completed. Output saved to {output_file}")
        print(f"Log saved to {log_file}")

    except Exception as e:
        print(f"Error processing file: {e}")


def gemini_translator(input_xml: str):
    process_xml_file_with_regex(input_xml)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translate XML file using Gemini AI")

    parser.add_argument(
        "-f",
        "--input_file",
        type=str,
        required=True,
        help="Path file to be translated",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default="./prompt_Sinhala_English.md",
        help="Path to the prompt file (default: ./prompt_Sinhala_English.md)",
    )

    args = parser.parse_args()

    # Validate if file exists
    if not Path(args.input_file).is_file():
        print(f"Error: Input file '{args.input_file}' does not exist")
        exit(1)

    gemini_translator(args.input_file)
