"""Independent, hand-computable tests for the closed-loop decision contract.

Run with ``python3 -B test_run.py`` from this directory.
No input traces, previously generated results, or network services are read.
"""

import unittest

import kernel_v1
from run import Loop, forecast, select_victims


class ForecastTests(unittest.TestCase):
    def f(self, mode, **changes):
        values = dict(t0=10., handler_at=10.25, delivered_at=10.375,
                      true_at=None, now=10.5, d=2., fixed=2.3, residual=.125)
        values.update(changes)
        return forecast(mode, **values)

    def test_eligible_signal_uses_handler_occurrence_not_delivery(self):
        self.assertAlmostEqual(self.f('signal'), 12.375)
        self.assertAlmostEqual(self.f('calibrated'), 12.375)

    def test_delivery_at_cutoff_is_eligible(self):
        self.assertAlmostEqual(self.f('calibrated', now=10.375), 12.375)

    def test_recorded_handler_is_not_usable_before_delivery(self):
        self.assertAlmostEqual(self.f('signal', now=10.374), 12.)
        self.assertAlmostEqual(self.f('calibrated', now=10.374), 12.3)

    def test_missing_delivery_invokes_the_same_fallback(self):
        self.assertAlmostEqual(self.f('signal', delivered_at=None), 12.)
        self.assertAlmostEqual(self.f('calibrated', delivered_at=None), 12.3)

    def test_no_event_has_same_fallback(self):
        self.assertAlmostEqual(self.f('signal', handler_at=None, delivered_at=None), 12.)
        self.assertAlmostEqual(self.f('calibrated', handler_at=None, delivered_at=None), 12.3)

    def test_fixed_and_configured_do_not_use_eligible_signal(self):
        self.assertAlmostEqual(self.f('fixed'), 12.3)
        self.assertAlmostEqual(self.f('configured'), 12.)

    def test_hidden_truth_is_rejected_outside_oracle(self):
        for m in ('configured', 'fixed', 'signal', 'calibrated'):
            with self.subTest(m=m):
                with self.assertRaises(ValueError):
                    self.f(m, true_at=11.)
        with self.assertRaises(ValueError):
            self.f('oracle')
        self.assertAlmostEqual(self.f('oracle', true_at=11.), 11.)
        self.assertAlmostEqual(self.f('oracle', true_at=25.), 25.)

    def test_perturbing_undelivered_future_event_cannot_change_action_input(self):
        for m in ('fixed', 'signal', 'calibrated'):
            with self.subTest(m=m):
                self.assertEqual(self.f(m, handler_at=10.51, delivered_at=10.9),
                                 self.f(m, handler_at=10.7, delivered_at=11.9))


class SelectionTests(unittest.TestCase):
    def g(self, **changes):
        values = dict(free=1., required=4., retiring=[(10.5, 3.)],
                      candidates=[('old', 3.), ('new', 4.)], now=10., max_wait=1.)
        values.update(changes)
        return select_victims(**values)

    def test_spare_capacity_bypasses_forecast_and_eviction(self):
        self.assertEqual(self.g(free=4.), ((), None))

    def test_wait_until_first_projected_fit_within_common_horizon(self):
        self.assertEqual(self.g(), ((), 10.5))
        self.assertEqual(self.g(retiring=[(10.8, 2.), (10.2, 1.), (11.4, 9.)]),
                         ((), 10.8))

    def test_due_at_horizon_is_eligible(self):
        self.assertEqual(self.g(retiring=[(11., 3.)]), ((), 11.))

    def test_due_after_horizon_does_not_cancel_legal_eviction(self):
        victims, _ = self.g(retiring=[(11.1, 3.)])
        self.assertEqual(victims, ('old',))

    def test_past_or_present_prediction_does_not_credit_reserved_memory(self):
        self.assertEqual(self.g(retiring=[(9.9, 3.), (10., 9.)]), (('old',), None))

    def test_foreseen_partial_return_only_offsets_needed_idle_victims(self):
        self.assertEqual(self.g(retiring=[(10.1, 1.)], candidates=[('A', 2.), ('B', 2.)]),
                         (('A',), None))

    def test_eviction_in_lru_order_stops_at_required_capacity(self):
        victims, _ = self.g(retiring=[], candidates=[('A', 1.), ('B', 2.), ('C', 8.)])
        self.assertEqual(victims, ('A', 'B'))

    def test_no_eligible_victim_cannot_produce_a_victim(self):
        victims, _ = self.g(retiring=[], candidates=[])
        self.assertEqual(victims, ())

    def test_counterfactual_information_changes_actions_with_same_inputs(self):
        inputs = dict(t0=10., handler_at=10.25, delivered_at=10.375,
                      true_at=None, now=11.5, d=2., fixed=4., residual=.125)
        selector_inputs = dict(free=0., required=4., candidates=[('idle-Z', 4.)],
                               now=inputs['now'], max_wait=1.)
        fixed = forecast('fixed', **inputs)
        signal = forecast('calibrated', **inputs)
        self.assertEqual(select_victims(retiring=[(signal, 4.)], **selector_inputs),
                         ((), 12.375))
        self.assertEqual(select_victims(retiring=[(fixed, 4.)], **selector_inputs)[0],
                         ('idle-Z',))
        missing = forecast('calibrated', **dict(inputs, delivered_at=None))
        self.assertEqual(select_victims(retiring=[(fixed, 4.)], **selector_inputs),
                         select_victims(retiring=[(missing, 4.)], **selector_inputs))


class FrozenKernelIntegrationFixture(unittest.TestCase):
    def test_forecast_changes_real_retirement_without_early_credit(self):
        # Exercise the public helpers against hand-computable event/ledger
        # outcomes in the pinned physics. This test adapter is deliberately
        # independent of the experiment runner's production subclass.
        from kernel_v1 import Kernel, NativeApp, Request

        class Fixture(Kernel):
            def __init__(self, mode):
                self.mode = mode
                super().__init__([NativeApp(app, 4.) for app in ('X', 'Y', 'Z')],
                                 [Request('x', 'X', 0., .5), Request('z', 'Z', 0., .5),
                                  Request('y', 'Y', 2., 1.), Request('z2', 'Z', 3., .5)],
                                 capacity_gb=8., prep_sampler=lambda *args: .5,
                                 return_sampler=lambda app, *args: 1.8 if app.app_id == 'X' else .75)

            def _make_room(self, app_id):
                retiring = []
                for item in self._instances.values():
                    if item.app_id == 'X' and item.state == 'retiring':
                        r = forecast(self.mode, t0=item.retirement_intent, handler_at=1.25,
                                     delivered_at=1.5, true_at=None, now=self.now,
                                     d=1., fixed=3., residual=.5)
                        if r >= self.now:
                            retiring.append((r, 4.))
                idle = sorted((p for p in self._instances.values() if p.state == 'idle'),
                              key=lambda p: (p.idle_since, p.ident))
                victims, _ = select_victims(8. - self.reserved_gb, self.configs[app_id].memory_gb,
                                            retiring, [(p.ident, 4.) for p in idle], self.now, 1.)
                for p in victims:
                    self._retire(self._instances[p], 'capacity')

        results = {}
        for mode in ('fixed', 'calibrated'):
            k = Fixture(mode)
            k.run_until(1.)
            self.assertEqual(k.apply_actions({'X': 1})['X']['executed'], 1)
            k.run_until(2.5)
            self.assertEqual(k.reserved_gb, 8.)
            self.assertIsNone(k._requests['y'].claim_at)
            self.assertEqual(k._reserved['retiring'], 8. if mode == 'fixed' else 4.)
            result = k.finish(5., 700.)
            self.assertEqual(result['original_request_denominator'], 4)
            self.assertEqual(result['completed_requests'], 4)
            self.assertEqual(result['remaining_reserved_gb'], 0.)
            self.assertAlmostEqual(result['reservation_gb_seconds'],
                                   sum(sum(p['cost_gbs'].values()) for p in result['instance_rows']))
            results[mode] = {q['request_id']: q for q in result['completed_request_rows']}
        self.assertAlmostEqual(results['fixed']['y']['other_wait'], .75)
        self.assertAlmostEqual(results['calibrated']['y']['other_wait'], .8)
        self.assertTrue(results['fixed']['z2']['cold'])
        self.assertFalse(results['calibrated']['z2']['cold'])


class ProductionLoopFixture(unittest.TestCase):
    def make(self, mode, treatment='natural', later_service=.5):
        from kernel_v1 import NativeApp, Request

        # Fixed seed 7 maps the first X and Z retirements to sample 0 and
        # sample 1 respectively. Both arms have the same hidden samples.
        return Loop([NativeApp(app, 4.) for app in ('X', 'Y', 'Z')],
                    [Request('x', 'X', 0., .5), Request('z', 'Z', 0., .5),
                     Request('y', 'Y', 2., 1.), Request('z2', 'Z', 3., later_service)],
                    day='fixture', d=1., mode=mode, treatment=treatment,
                    physics=dict(seed=7, prep_s=[.5]),
                    law=([(.25, .5, 1.8, 'X-calibration'),
                          (.05, .1, .75, 'Z-calibration')], 3., .5),
                    capacity_gb=8.)

    def reach_first_decision(self, mode, treatment='natural', later_service=.5):
        k = self.make(mode, treatment, later_service)
        k.run_until(1.)
        self.assertEqual(k.apply_actions({'X': 1})['X']['executed'], 1)
        k.run_until(2.5)
        self.assertEqual(k.reserved_gb, 8.)
        self.assertIsNone(k._requests['y'].claim_at)
        return k

    def test_real_loop_records_matched_state_action_change(self):
        fixed = self.reach_first_decision('fixed')
        hybrid = self.reach_first_decision('calibrated')
        self.assertEqual(len(fixed.decision_rows), 1)
        self.assertEqual(len(hybrid.decision_rows), 1)
        tick0, tick1 = fixed.decision_rows[0], hybrid.decision_rows[0]
        for k in ('t', 'app', 'head', 'free_gb', 'required_gb', 'pending'):
            self.assertEqual(tick0[k], tick1[k])
        self.assertEqual(len(tick0['victims']), 1)
        self.assertEqual(tick1['victims'], ())
        self.assertEqual(tick1['fixed_shadow_victims'], tick0['victims'])
        self.assertEqual(tick1['wait_until'], 2.75)
        self.assertEqual(fixed._reserved['retiring'], 8.)
        self.assertEqual(hybrid._reserved['retiring'], 4.)
        # The forecast says 2.75, but X's actual return is 2.80.
        hybrid.run_until(2.75)
        self.assertIsNone(hybrid._requests['y'].claim_at)
        hybrid.run_until(2.80)
        self.assertEqual(hybrid._requests['y'].claim_at, 2.80)
        self.assertEqual(fixed.finish(5.)['completed_requests'], 4)
        self.assertEqual(hybrid.finish(5.)['completed_requests'], 4)

    def test_no_delivery_gives_fixed_actions_and_matched_request_outcomes(self):
        fixed = self.reach_first_decision('fixed', 'missing')
        hybrid = self.reach_first_decision('calibrated', 'missing')
        fixed_summary, hybrid_summary = fixed.finish(5.), hybrid.finish(5.)
        self.assertEqual(fixed.decision_rows, hybrid.decision_rows)
        for k in ('completed_request_rows', 'instance_rows', 'cost_by_state_gbs'):
            self.assertEqual(fixed_summary[k], hybrid_summary[k])

    def test_future_service_mutation_cannot_change_decision_prefix(self):
        first = self.reach_first_decision('calibrated')
        changed = self.reach_first_decision('calibrated', later_service=9.)
        self.assertEqual(first.decision_rows, changed.decision_rows)


if __name__ == '__main__':
    unittest.main()
