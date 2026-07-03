import time

N = 100000

# ---- Standard Sieve of Eratosthenes ----
def standard_sieve(n):
    sieve = bytearray(b'\x01') * (n + 1)
    sieve[0] = 0
    sieve[1] = 0
    for i in range(2, int(n**0.5) + 1):
        if sieve[i]:
            sieve[i*i:n+1:i] = b'\x00' * ((n - i*i) // i + 1)
    return [i for i in range(2, n+1) if sieve[i]]


# ---- Optimised Wheel Sieve (wheel of 2·3·5·7 = 210) ----
def wheel_sieve(n):
    # First mark multiples of 2, 3, 5, 7
    is_prime = bytearray(b'\x01') * (n + 1)
    is_prime[0] = 0
    is_prime[1] = 0

    for p in [2, 3, 5, 7]:
        start = p * p
        if start <= n:
            is_prime[start:n+1:p] = b'\x00' * ((n - start) // p + 1)

    # Then sieve remaining primes from 11 to sqrt(N)
    for i in range(11, int(n**0.5) + 1):
        if is_prime[i]:
            is_prime[i*i:n+1:i] = b'\x00' * ((n - i*i) // i + 1)

    return [i for i in range(2, n+1) if is_prime[i]]


# ---- Timing and verification ----
primes_std = standard_sieve(N)
t0 = time.perf_counter()
_ = standard_sieve(N)
t1 = time.perf_counter()
time_std = t1 - t0

primes_wheel = wheel_sieve(N)
t0 = time.perf_counter()
_ = wheel_sieve(N)
t1 = time.perf_counter()
time_wheel = t1 - t0

print(f"Standard sieve found {len(primes_std)} primes")
print(f"Wheel sieve   found  {len(primes_wheel)} primes")
print(f"Expected:       9592")
print(f"\nTime (standard): {time_std*1000:.3f} ms")
print(f"Time (wheel)    : {time_wheel*1000:.3f} ms")

if len(primes_std) == len(primes_wheel) and primes_std == primes_wheel:
    print("\n✓ Both sieves produce identical prime sets!")
else:
    print("\n✗ MISMATCH detected between the two sieves.")


if len(primes_std) == 9592:
    print("✓ Standard sieve found exactly 9592 primes")
else:
    print(f"✗ Standard sieve found {len(primes_std)} primes (expected 9592)")

if len(primes_wheel) == 9592:
    print("✓ Wheel sieve found exactly 9592 primes")
else:
    print(f"✗ Wheel sieve found {len(primes_wheel)} primes (expected 9592)")


if time_std < time_wheel:
    print("\n→ Standard sieve is faster")
elif time_wheel < time_std:
    print("\n→ Wheel sieve is faster")
else:
    print("\n→ Both took the same time")
