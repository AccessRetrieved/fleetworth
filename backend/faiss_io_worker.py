"""FAISS serialization boundary. Never import Torch in this process."""
import sys
from pathlib import Path

import faiss
import numpy as np


def main(operation, source, target):
    if operation == "encode":
        vectors = np.load(source, allow_pickle=False)
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
        # Exercise the native search runtime in isolation too.
        if index.ntotal:
            index.search(vectors[:1], 1)
        Path(target).write_bytes(faiss.serialize_index(index).tobytes())
    elif operation == "decode":
        index = faiss.deserialize_index(np.frombuffer(Path(source).read_bytes(), dtype=np.uint8))
        if not isinstance(index, faiss.IndexFlatIP):
            raise ValueError("Expected a flat inner-product index")
        with open(target, "wb") as output:
            np.save(output, index.reconstruct_n(0, index.ntotal), allow_pickle=False)
    else:
        raise ValueError("Unknown conversion operation")


if __name__ == "__main__":
    main(*sys.argv[1:])
