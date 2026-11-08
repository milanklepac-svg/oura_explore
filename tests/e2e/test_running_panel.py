"""Running UI behavior with invented browser-only responses; no provider requests."""
import os
import unittest

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class RunningPanelBrowserTests(unittest.TestCase):
    def test_sources_missing_data_and_manual_sync(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--window-size=1440,1100')
        driver = webdriver.Remote(command_executor=os.environ['SELENIUM_URL'], options=options)
        try:
            wait = WebDriverWait(driver, 25)
            driver.get(os.environ['TEST_WEB_URL'])
            panel = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.panel.running')))
            refresh = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '.running-refresh')))
            driver.execute_script("""
                window.runOriginalFetch = window.fetch;
                window.runRequests = {context: 0, status: 0, sync: 0};
                window.runContext = {
                    start: '2026-01-01', end: '2026-01-28',
                    summary: {recorded_runs: 3, recorded_km: 12, missing_distance: 1,
                              missing_suunto_distance: 1, ambiguous_overlap_records: 1},
                    runs: [
                        {day: '2026-01-28', km: 7, minutes: 42, source: 'suunto',
                         source_fallback: false, reconciliation: 'matched_suunto_preferred',
                         duration_basis: 'elapsed_time', elapsed_pace_minutes_per_km: 6},
                        {day: '2026-01-27', km: 5, minutes: 30, source: 'oura',
                         source_fallback: true, reconciliation: 'oura_only',
                         provenance: 'Synthetic <img src=x onerror="window.runInjected=true">'},
                        {day: '2026-01-26', km: null, minutes: null, source: 'suunto',
                         source_fallback: false, reconciliation: 'ambiguous_overlap'}
                    ], limitations: ['Synthetic coverage limit.']
                };
                window.runStatus = {configured: true, last_successful_sync_at: '2026-01-28T12:00:00Z',
                    sync: null, quota: {requests_used: 4, requests_remaining: 96, blocked_until: null}};
                window.fetch = (url, options) => {
                    let data, code = 200;
                    if (url === '/api/running/context?include_today=true') {
                        window.runRequests.context++; data = window.runContext;
                        code = window.runContextCode || 200;
                    } else if (url === '/api/suunto/status') {
                        window.runRequests.status++; data = window.runStatus;
                        code = window.runStatusCode || 200;
                    } else if (url === '/api/suunto/sync') {
                        window.runRequests.sync++;
                        window.runSyncMethod = options.method;
                        data = {job_id: 'synthetic-job', status: 'queued', detail: 'Waiting for worker'};
                    } else return window.runOriginalFetch(url, options);
                    return Promise.resolve(new Response(JSON.stringify(data), {
                        status: code, headers: {'Content-Type': 'application/json'}
                    }));
                };
            """)
            refresh.click()
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.running-item')) == 3)
            self.assertIn('Suunto preferred', panel.text)
            self.assertIn('Oura fallback', panel.text)
            self.assertIn('Ambiguous overlap · review separately', panel.text)
            self.assertIn('Distance: Not recorded', panel.text)
            self.assertIn('Duration: Not recorded', panel.text)
            self.assertIn('Duration basis: elapsed time', panel.text)
            self.assertIn('6 min/km', panel.text)
            self.assertIn('current day, which may still be partial', panel.text)
            self.assertIn('not planning totals', panel.text)
            self.assertIn('Synthetic coverage limit.', panel.text)
            self.assertIn('4 used · 96 remaining', panel.text)
            self.assertEqual([item.text for item in panel.find_elements(By.CSS_SELECTOR, '.running-summary dd')],
                             ['3', '12', '1', '1', '1'])
            self.assertIn('<img src=x', panel.text)
            self.assertFalse(panel.find_elements(By.TAG_NAME, 'img'))
            self.assertIsNone(driver.execute_script('return window.runInjected'))

            sync = panel.find_element(By.CSS_SELECTOR, '.suunto-sync')
            sync.click()
            wait.until(lambda d: 'Suunto sync pending' in sync.text)
            self.assertFalse(sync.is_enabled())
            self.assertIn('Waiting for worker', panel.text)
            self.assertEqual(driver.execute_script('return window.runSyncMethod'), 'POST')
            self.assertEqual(driver.execute_script('return window.runRequests.sync'), 1)
            # Waiting for worker completion does not require automatic provider polling.
            self.assertEqual(driver.execute_script('return window.runRequests.status'), 1)

            driver.execute_script("window.runStatus.sync = {job_id:'synthetic-job',status:'running',detail:'Worker active'}")
            refresh.click()
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, '.running-sync-state'), 'running'))
            self.assertFalse(sync.is_enabled())
            driver.execute_script("""
                window.runStatus.sync = {status:'succeeded', detail:'Synthetic sync complete'};
                window.runStatus.configured = false;
                window.runStatus.quota.blocked_until = '2026-01-29T00:00:00Z';
                window.runContext.runs = [];
                window.runContext.summary = {recorded_runs:0, recorded_km:0, missing_distance:0,
                    missing_suunto_distance:0, ambiguous_overlap_records:0};
            """)
            refresh.click()
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, '.running .empty'), 'No recorded runs'))
            self.assertIn('not configured', panel.text)
            self.assertIn('Blocked until 2026-01-29', panel.text)
            self.assertFalse(sync.is_enabled())

            driver.execute_script('window.runContextCode = 503; window.runStatusCode = 503')
            refresh.click()
            wait.until(lambda d: len(panel.find_elements(By.CSS_SELECTOR, '[role="alert"]')) == 2)
            self.assertIn('Running records are unavailable', panel.text)
            self.assertIn('Suunto status is unavailable', panel.text)
            self.assertFalse(sync.is_enabled())
            self.assertTrue(refresh.is_enabled())
            self.assertFalse(panel.find_elements(By.CSS_SELECTOR, '.running-summary'))
        finally:
            driver.quit()
