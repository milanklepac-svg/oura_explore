"""Synthetic race plan browser flow; uses only the isolated test API."""
import os
import unittest
from datetime import date, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class MarathonMethodBrowserTests(unittest.TestCase):
    def test_explicit_save_persists_method_and_unsaved_selection_does_not(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--window-size=1440,1100')
        driver = webdriver.Remote(command_executor=os.environ['SELENIUM_URL'], options=options)
        try:
            driver.get(os.environ['TEST_WEB_URL'])
            wait = WebDriverWait(driver, 25)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.race-planner')))
            start = date.today()
            conf = dict(start=str(start), philosophy='foundation', history_confirmed=True,
                        weekly_km=40, peak_weekly_km=55, current_long_km=14,
                        easy_pace_minutes=6, max_long_minutes=150,
                        running_days=['mon', 'wed', 'fri', 'sun'], long_day='sun',
                        races=[dict(name='Synthetic browser marathon', day=str(start + timedelta(days=140)),
                                    priority='main', distance='marathon', target_minutes=240)])
            seeded = driver.execute_async_script('''
                const settings=arguments[0], done=arguments[1];
                fetch('/api/plan').then(r=>r.json()).then(d=>fetch('/api/plan', {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({settings, expected_revision:d.plan?.revision || 0})
                })).then(async r=>done({ok:r.ok, data:await r.json()}));
            ''', conf)
            self.assertTrue(seeded['ok'], seeded)
            driver.refresh()
            selector = (By.XPATH, "//label[contains(.,'Planning approach')]/select")
            wait.until(lambda d: Select(d.find_element(*selector)).first_selected_option.get_attribute('value') == 'foundation')
            details = driver.find_element(By.XPATH, "//summary[contains(.,'Edit race calendar')]/..")
            driver.execute_script('arguments[0].open=true', details)
            Select(driver.find_element(*selector)).select_by_value('marathon_endurance')
            self.assertIn('independent marathon endurance', driver.find_element(By.CSS_SELECTOR, '.race-planner').text)
            self.assertIn('40 current km/week', driver.find_element(By.CSS_SELECTOR, '.race-planner').text)
            driver.refresh()
            wait.until(lambda d: Select(d.find_element(*selector)).first_selected_option.get_attribute('value') == 'foundation')
            details = driver.find_element(By.XPATH, "//summary[contains(.,'Edit race calendar')]/..")
            driver.execute_script('arguments[0].open=true', details)
            Select(driver.find_element(*selector)).select_by_value('marathon_endurance')
            driver.find_element(By.XPATH, "//label[contains(.,'Optional modest marathon-effort')]/input").click()
            driver.find_element(By.XPATH, "//button[contains(.,'Save revised calendar')]").click()
            wait.until(EC.alert_is_present()).accept()
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, '.race-planner'), 'Plan saved locally'))
            driver.refresh()
            wait.until(lambda d: Select(d.find_element(*selector)).first_selected_option.get_attribute('value') == 'marathon_endurance')
            saved = driver.execute_async_script("const done=arguments[0]; fetch('/api/plan').then(r=>r.json()).then(done)")['plan']
            self.assertTrue(saved['settings']['marathon_effort'])
            work = [s['current'] for s in saved['sessions'] if s['current'].get('intensity') == 'M']
            self.assertTrue(work)
            self.assertTrue(all(s['phase'] == 'marathon_specific' for s in work))
            self.assertTrue(all('pace_minutes_per_km' not in b for s in work for b in s['segments']))
        finally:
            driver.quit()
