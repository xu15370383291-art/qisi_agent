"""Count integers whose prime factors are all in a supplied prime list.

The public :func:`count_valid_numbers` function follows the problem statement.
The module also exposes ``solution`` as a conventional online-judge entry point
and accepts either ``n nums...`` or ``n k nums...`` on standard input.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable


def count_valid_numbers(n: int, nums: Iterable[int]) -> int:
    """Return the count of valid integers in ``[1, n]``.

    A number is valid when every prime factor (with multiplicity ignored) is in
    ``nums``.  The number 1 is valid because it has no prime factors.

    The input list is expected to contain primes, as specified by the problem.
    Values outside ``[2, n]`` cannot be factors of a number being counted and
    are harmlessly ignored.  This implementation uses a smallest-prime-factor
    sieve, so each integer is checked from its smallest factor and its quotient.
    """
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 1:
        return 0

    allowed = {prime for prime in nums if isinstance(prime, int) and 2 <= prime <= n}
    if n == 1:
        return 1
    if not allowed:
        return 1

    # spf[x] is the smallest prime factor of x.  Building it with the linear
    # sieve keeps factor checks deterministic and avoids trial division.
    spf = [0] * (n + 1)
    primes: list[int] = []
    for value in range(2, n + 1):
        if spf[value] == 0:
            spf[value] = value
            primes.append(value)
        for prime in primes:
            multiple = prime * value
            if multiple > n or prime > spf[value]:
                break
            spf[multiple] = prime

    valid = bytearray(n + 1)
    valid[1] = 1
    count = 1
    for value in range(2, n + 1):
        factor = spf[value]
        quotient = value // factor
        # Removing one occurrence is enough: all remaining factors are checked
        # recursively through quotient, while ``factor`` must be whitelisted.
        if factor in allowed and valid[quotient]:
            valid[value] = 1
            count += 1
    return count


def solution(n: int, nums: Iterable[int]) -> int:
    """Compatibility alias for online-judge harnesses."""
    return count_valid_numbers(n, nums)


# A short alias is useful when the judge describes the operation simply as
# ``count`` rather than prescribing a class or method name.
count = count_valid_numbers


def _parse_stdin(data: str) -> tuple[int, list[int]]:
    values = [int(token) for token in data.split()]
    if not values:
        raise ValueError("input is empty")
    n = values[0]
    # Support both common formats: ``n nums...`` and ``n k nums...``.
    if len(values) >= 2 and values[1] == len(values) - 2:
        return n, values[2:]
    return n, values[1:]


if __name__ == "__main__":
    number, prime_list = _parse_stdin(sys.stdin.read())
    print(count_valid_numbers(number, prime_list))
