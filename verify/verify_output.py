import time
N = 100_000
def standard_sieve(n):
    is_prime = bytearray(b'\x01') * (n + 1)
    is_prime[0] = is_prime[1] = 0
    for i in range(2, int(n**0.5) + 1):
        if is_prime[i]:
            step = i
            start = i * i
            is_prime[start::step] = b'\x00' * ((n - start) // step + 1)
    return [i for i in range(2, n + 1) if is_prime[i]]
def wheel_sieve(n):
    is_prime = bytearray(b'\x01') * (n + 1)
    is_prime[0] = is_prime[1] = 0
    for p in (2, 3, 5, 7):
        start = p * p
        if start <= n:
            step = p
            is_prime[start::step] = b'\x00' * ((n - start) // step + 1)
    for i in range(11, int(n**0.5) + 1):
        if is_prime[i]:
            step = i
            start = i * i
            is_prime[start::step] = b'\x00' * ((n - start) // step + 1)
    return [i for i in range(2, n + 1) if is_prime[i]]
t_start = time.perf_counter()
primes_std = standard_sieve(N)
t_standard = time.perf_counter() - t_start
t_wheel_start = time.perf_counter()
primes_wheel = wheel_sieve(N)
t_wheel = time.perf_counter() - t_wheel_start
print(f"Standard sieve: {len(primes_std)} primes in {t_standard:.4f}s")
print(f"Wheel sieve:     {len(primes_wheel)} primes in {t_wheel:.4f}s")
std_set = set(primes_std)
wheel_set = set(primes_wheel)
print(f"Standard count:  {len(std_set)}")
print(f"Wheel count:     {len(wheel_set)}")
print(f"Identical?       {std_set == wheel_set}")
print(f"Expected?        {len(std_set) == 9592 and len(wheel_set) == 9592}")
t2 = time.perf_counter()
_ = standard_sieve(N)
t_standard_final = time.perf_counter() - t2
print(f"Standard sieve (final): {len(primes_std)} primes in {t_standard_final:.4f}s")