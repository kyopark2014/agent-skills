"""의자·방석 격자 이중선은 벽 키에서 뺀다."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib_walls import Seg, drop_seat_cushion_wall_keys


class SeatCushionWallKeyTests(unittest.TestCase):
    def test_repeated_short_wide_gap_pairs_dropped(self):
        segs: list[Seg] = []
        keys: set[tuple[int, int]] = set()
        # 380 mm H 선, y 간격 500 → 의자 열
        for i in range(8):
            y = i * 250  # 0,250,500,... so pairs at 0-500, 250-750, etc.
            s = Seg(0.0, float(y), 380.0, float(y), i, 0)
            segs.append(s)
            keys.add(s.key)
        # 긴 진짜 벽 짝 (길이·간격이 의자 패턴과 다름)
        long_a = Seg(0.0, -2000.0, 8000.0, -2000.0, 100, 0)
        long_b = Seg(0.0, -1800.0, 8000.0, -1800.0, 101, 0)
        segs.extend([long_a, long_b])
        keys |= {long_a.key, long_b.key}

        drop = drop_seat_cushion_wall_keys(segs, keys)

        self.assertGreaterEqual(len(drop), 6)
        self.assertTrue(any(k[0] < 100 for k in drop))
        self.assertNotIn(long_a.key, drop)
        self.assertNotIn(long_b.key, drop)


if __name__ == "__main__":
    unittest.main()
