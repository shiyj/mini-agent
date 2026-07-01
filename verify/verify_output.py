import time

N = 100000

# ---- Standard Sieve of Eratosthenes ----
def sieve_standard(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = False
    is_prime[1] = False
    for i in range(2, int(n**0.5) + 1):
        if is_prime[i]:
            for j in range(i*i, n+1, i):
                is_prime[j] = False
    return [i for i in range(2, n+1) if is_prime[i]]

# ---- Wheel Sieve (wheel of 2*3*5*7 = 210) ----
def sieve_wheel(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = False
    is_prime[1] = False
    
    # Mark multiples of the first four primes: 2, 3, 5, 7
    for p in (2, 3, 5, 7):
        for j in range(p*p, n+1, p):
            is_prime[j] = False
    
    # For i from 11 to sqrt(n), if still prime, mark its multiples
    limit = int(n**0.5) + 1
    for i in range(11, limit):
        if is_prime[i]:
            for j in range(i*i, n+1, i):
                is_prime[j] = False
    
    return [i for i in range(2, n+1) if is_prime[i]]

# ---- Main verification ----
t0 = time.perf_counter()
primes_std = sieve_standard(N)
t1 = time.perf_counter()
print(f"Standard Sieve:   {len(primes_std)} primes in {t1-t0:.6f}s")

t2 = time.perf_counter()
primes_wheel = sieve_wheel(N)
t3 = time.perf_counter()
print(f"Wheel Sieve:      {len(primes_wheel)} primes in {t3-t2:.6f}s")

# Verify both find exactly 9592 primes
assert len(primes_std) == 9592, f"Standard sieve found {len(primes_std)} primes (expected 9592)"
assert len(primes_wheel) == 9592, f"Wheel sieve found {len(primes_wheel)} primes (expected 9592)"

# Verify identical sets
assert primes_std == primes_wheel, "Prime sets differ!"

print(f"Both sieves produce identical prime sets with {len(primes_std)} primes each.")
