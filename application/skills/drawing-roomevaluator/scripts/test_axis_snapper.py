"""단링크 체인 가장자리 벽 좌표가 snap 에서 버려지지 않는지 확인한다."""

import unittest

import evaluate_room as room


class AxisSnapperTest(unittest.TestCase):
    def test_edge_of_long_chain_is_not_dropped(self):
        # 15 mm 간격으로 이어진 긴 체인 + 분리 가로벽(체인 끝에서 멀리).
        chain = [34153.0 + 2.0 * i for i in range(100)]  # span ~198 mm
        separator = 34365.83
        values = sorted(chain + [separator, 34615.8])
        snap, centers = room._axis_snapper(values, room.CLUSTER_TOL_MM)
        self.assertIsNotNone(snap(separator))
        self.assertIsNotNone(snap(34615.8))
        # 가장자리도 어떤 축으로든 살아 있어야 한다.
        self.assertLessEqual(abs(snap(separator) - separator), room.CLUSTER_TOL_MM)
        # 체인 중앙값 하나로만 묶이지 않아 여러 center 가 생긴다.
        self.assertGreater(len(centers), 1)

    def test_nearby_values_still_share_a_center(self):
        values = [1000.0, 1005.0, 1010.0, 2000.0, 2008.0]
        snap, centers = room._axis_snapper(values, room.CLUSTER_TOL_MM)
        self.assertEqual(snap(1000.0), snap(1010.0))
        self.assertEqual(snap(2000.0), snap(2008.0))
        self.assertNotEqual(snap(1000.0), snap(2000.0))
        self.assertEqual(len(centers), 2)


if __name__ == "__main__":
    unittest.main()
