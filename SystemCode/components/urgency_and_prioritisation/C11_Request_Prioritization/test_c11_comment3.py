"""C11 Comment 3 contract tests; synthetic inputs, not NYC operational validation."""
import csv
import unittest
from pathlib import Path
from c11_final import prioritize_from_c10

ROOT = Path(__file__).resolve().parent
NOW = '2026-10-13T12:00:00+00:00'

class Comment3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / 'demo_scenarios.csv').open(encoding='utf-8-sig', newline='') as f:
            sample = next(csv.DictReader(f))
        cls.base = dict(sample, request_id='PRIMARY', agency=sample['expected_agency'],
                        emergency_action='CONTINUE', created_date='2026-10-12T12:00:00+00:00')

    def run_one(self, **changes):
        return prioritize_from_c10([dict(self.base, **changes)], NOW)[0]

    def test_independent_reporters_increase_score(self):
        one = self.run_one(corroboration_count=1)
        ten = self.run_one(corroboration_count=10)
        self.assertEqual(ten['priority_score'] - one['priority_score'], 10)

    def test_duplicate_is_held(self):
        item = self.run_one(request_id='DUP', relation='DUPLICATE', is_primary=False,
                            duplicate_of='PRIMARY', corroboration_count=10)
        self.assertEqual(item['c11_queue_status'], 'hold')
        self.assertIsNone(item['overall_queue_position'])
        self.assertFalse(item['dispatch_approved'])

    def test_group_count_propagates_to_primary(self):
        primary = dict(self.base, duplicate_group_id='G1', is_primary=True, corroboration_count=1)
        duplicate = dict(self.base, request_id='DUP', duplicate_group_id='G1',
                         relation='DUPLICATE', is_primary=False, duplicate_of='PRIMARY', corroboration_count=10)
        results = prioritize_from_c10([primary, duplicate], NOW)
        self.assertEqual(results[0]['corroboration_count'], 10)
        self.assertEqual(results[0]['overall_queue_position'], 1)
        self.assertIsNone(results[1]['overall_queue_position'])

    def test_escalation_needs_approval(self):
        baseline = self.run_one(escalation_level='NONE')
        unapproved = self.run_one(escalation_level='CENTRAL_AUTHORITY')
        approved = self.run_one(escalation_level='CENTRAL_AUTHORITY', escalation_approved=True)
        self.assertEqual(unapproved['priority_score'], baseline['priority_score'])
        self.assertEqual(approved['priority_score'] - baseline['priority_score'], 25)

    def test_bad_contract_rejected(self):
        for change in ({'corroboration_count': -1}, {'escalation_level': 'UNKNOWN'},
                       {'relation': 'DUPLICATE', 'is_primary': False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_one(**change)

if __name__ == '__main__':
    unittest.main(verbosity=2)
