#!/usr/bin/env python3
"""
Sieve of Eratosthenes comparison: standard vs wheel-optimized version.
N = 100_000, expected prime count = 9592.
"""

import time
import math


def standard_sieve(n: int) -> list[int]:
    """Standard Sieve of Eratosthenes."""
    is_prime = bytearray(b'\x01') * (n + 1)
    is_prime[0] = is_prime[1] = 0
    for p in range(2, int(math.isqrt(n)) + 1):
        if is_prime[p]:
            step = p * p
            while step <= n:
                is_prime[step] = 0
                step += p
    return [i for i in range(2, n + 1) if is_prime[i]]


def wheel_sieve(n: int) -> list[int]:
    """Wheel sieve: pre-mark multiples of 2,3,5,7 then continue normally."""
    is_prime = bytearray(b'\x01') * (n + 1)
    is_prime[0] = is_prime[1] = 0

    # First pass: mark multiples of the first four primes
    for p in (2, 3, 5, 7):
        step = p * p
        while step <= n:
            is_prime[step] = 0
            step += p

    # Second pass: for remaining i from 11 to sqrt(N)
    limit = int(math.isqrt(n)) + 1
    for i in range(11, limit):
        if is_prime[i]:
            step = i * i
            while step <= n:
                is_prime[step] = 0
                step += i

    return [i for i in range(2, n + 1) if is_prime[i]]


N = 100_000
EXPECTED_COUNT = 9592

# --- Standard sieve timing ---
start = time.perf_counter()
primes_std = standard_sieve(N)
t_std = time.perf_counter() - start

# --- Wheel sieve timing ---
start = time.perf_counter()
primes_wheel = wheel_sieve(N)
t_wheel = time.perf_counter() - start

# --- Verification ---
print(f"Standard Sieve:  {len(primes_std)} primes in {t_std:.6f}s")
print(f"Wheel Sieve:     {len(primes_wheel)} primes in {t_wheel:.6f}s")
print(f"Expected count:  {EXPECTED_COUNT}")

assert len(primes_std) == EXPECTED_COUNT, "Standard sieve did not find the expected number of primes!"
assert len(primes_wheel) == EXPECTED_COUNT, "Wheel sieve did not find the expected number of primes!"

if set(primes_std) == set(primes_wheel):
    print("✓ Both sieves produce identical prime sets.")
else:
    diff = sorted(set(primes_std).symmetric_difference(set(primes_wheel)))
    print(f"✗ Mismatch! Difference: {diff}")

if t_std < t_wheel:
    print(f"→ Standard sieve is faster (by {t_wheel - t_std:.6f}s)")
elif t_wheel < t_std:
    print(f"→ Wheel sieve is faster (by {t_std - t_wheel:.6f}s)")
else:
    print("→ Both sieves took the same time.")

print("\nDone!")
