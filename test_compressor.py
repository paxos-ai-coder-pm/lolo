import os
import random
import unittest

from compressor import compress, decompress


class RoundTripTests(unittest.TestCase):
    def check(self, data):
        self.assertEqual(decompress(compress(data)), data)

    def test_empty(self):
        self.check(b"")

    def test_single_byte(self):
        self.check(b"a")

    def test_repeated_byte(self):
        self.check(b"z" * 10000)

    def test_two_symbols(self):
        self.check(b"ab" * 500 + b"a")

    def test_all_byte_values(self):
        self.check(bytes(range(256)) * 4)

    def test_random_bytes(self):
        rng = random.Random(1234)
        self.check(bytes(rng.randrange(256) for _ in range(5000)))

    def test_skewed_distribution(self):
        rng = random.Random(42)
        # Fibonacci-like weights produce deep Huffman trees
        weights = [1, 1]
        while len(weights) < 30:
            weights.append(weights[-1] + weights[-2])
        self.check(bytes(rng.choices(range(30), weights=weights, k=20000)))

    def test_text(self):
        self.check("héllo wörld\n\ttabs and unicode ✓\n".encode("utf-8") * 50)

    def test_this_file(self):
        with open(os.path.abspath(__file__), "rb") as f:
            self.check(f.read())


if __name__ == "__main__":
    unittest.main()
