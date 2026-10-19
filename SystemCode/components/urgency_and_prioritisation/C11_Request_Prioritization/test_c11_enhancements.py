"""Additional regression tests for uppercase contract, review policy and validation."""
import unittest
from pathlib import Path
from c11_final import prioritize_from_c10
from c11_prioritization import prioritize, score_request, parse_time

ROOT = Path(__file__).resolve().parent
NOW = '2026-10-13T12:00:00+00:00'

class EnhancedC11Tests(unittest.TestCase):
    def test_uppercase_and_banding(self):
        rows = [dict(request_id=k, agency='DOT', c11_urgency=u, created_date='2026-10-12T12:00:00+00:00')
                for k,u in [('low','Low'),('high','HIGH'),('critical','Critical'),('medium','MEDIUM')]]
        out = prioritize(rows, NOW)
        self.assertEqual([(r['urgency_level'],r['queue_position']) for r in out],
                         [('CRITICAL','C1'),('HIGH','H1'),('MEDIUM','M1'),('LOW','L1')])
        self.assertTrue(all(r['dispatch_approved'] is False for r in out))

    def test_review_is_ranked_but_not_approved(self):
        rows = [dict(request_id='REVIEW',agency='DOT',c11_urgency='HIGH',c11_queue_status='provisional_review')]
        out = prioritize(rows, NOW)[0]
        self.assertEqual(out['queue_position'], 'H1')
        self.assertEqual(out['priority_band'], 'PROVISIONAL_REVIEW')
        self.assertFalse(out['dispatch_approved'])

    def test_hold_is_unranked(self):
        out = prioritize([dict(request_id='HOLD',agency='DOT',c11_queue_status='hold')], NOW)[0]
        self.assertIsNone(out['queue_position'])
        self.assertFalse(out['dispatch_approved'])

    def test_bad_flags_rejected(self):
        for value in ('maybe', 'unknown', 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prioritize([dict(request_id='BAD',agency='DOT',c11_urgency='HIGH',approved_escalation=value)], NOW)

    def test_bad_sla_rejected(self):
        for value in ('nan', 'inf', '-5', '0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prioritize([dict(request_id='BAD',agency='DOT',c11_urgency='HIGH',created_date='2026-10-12T12:00:00+00:00',sla_target_hours=value)], NOW)

    def test_c10_integration_uppercase(self):
        import csv
        with (ROOT/'demo_scenarios.csv').open(encoding='utf-8-sig', newline='') as f:
            sample=next(csv.DictReader(f))
        sample.update(request_id='INTEGRATION', agency=sample['expected_agency'],emergency_action='CONTINUE',created_date='2026-10-12T12:00:00+00:00')
        out = prioritize_from_c10([sample], NOW, root=ROOT)[0]
        self.assertIn(out['c11_urgency'], ('CRITICAL','HIGH','MEDIUM','LOW'))
        self.assertEqual(out['c11_urgency'], out['urgency_level'])
        self.assertFalse(out['dispatch_approved'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
