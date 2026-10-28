"""Memory eval runner (skeleton).

Loads and validates a split and the traps file, then plays its cases. There are no cases yet and
nothing to play them against: the agent arrives in phase 3 and memory writes in phase 4. Until
then this checks that the data is well formed.

    python ../evals/memory_eval.py --split dev
"""

import argparse
import sys

from memory_cases import SPLITS, InvalidDataset, load_cases, load_traps


def main(split: str) -> int:
    traps = load_traps()
    cases = load_cases(split)
    print(f"{split}: {len(cases)} cases, {len(traps)} traps defined")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=SPLITS, default="dev")
    arguments = parser.parse_args()
    try:
        sys.exit(main(arguments.split))
    except InvalidDataset as error:
        sys.exit(f"error: {error}")
