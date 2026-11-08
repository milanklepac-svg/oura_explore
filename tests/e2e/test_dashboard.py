"""Headless Chromium exercises the built UI and real same-origin API."""
import os
import unittest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class DashboardBrowserTests(unittest.TestCase):
    def test_dashboard_save_reload_and_review(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--window-size=1440,1100')
        driver = webdriver.Remote(command_executor=os.environ['SELENIUM_URL'], options=options)
        try:
            wait = WebDriverWait(driver, 25)
            driver.get(os.environ['TEST_WEB_URL'])
            wait.until(EC.text_to_be_present_in_element((By.TAG_NAME, 'h1'), 'Recovery meets movement.'))
            wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.cards article')) == 6)
            sleep_card = driver.find_element(By.XPATH, "//article[p[text()='Sleep score']]/strong")
            wait.until(lambda d: sleep_card.text == '81')
            wait.until(EC.presence_of_element_located((By.XPATH, "//label[contains(.,'Running goal')]/select")))
            goal = Select(driver.find_element(By.XPATH, "//label[contains(.,'Running goal')]/select"))
            goal.select_by_value('half_marathon')
            note = driver.find_element(By.XPATH, "//label[contains(.,'Goal note')]/input")
            note.clear()
            note.send_keys('Synthetic browser preference')
            driver.find_element(By.XPATH, "//button[text()='Save training settings']").click()
            wait.until(lambda d: any('Training settings saved' in element.text
                                     for element in d.find_elements(By.CSS_SELECTOR, '.training .form-actions')))
            driver.refresh()
            wait.until(lambda d: Select(d.find_element(By.XPATH, "//label[contains(.,'Running goal')]/select")).first_selected_option.get_attribute('value') == 'half_marathon')
            self.assertEqual(driver.find_element(By.XPATH, "//label[contains(.,'Goal note')]/input").get_attribute('value'), 'Synthetic browser preference')
            planned = Select(driver.find_element(By.XPATH, "//label[contains(.,'Planned session')]/select"))
            planned.select_by_value('rest')
            driver.find_element(By.XPATH, "//button[text()='Review session']").click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.guidance h3')))
            self.assertEqual(driver.find_element(By.CSS_SELECTOR, '.guidance h3').text.lower(), 'keep rest')
            self.assertFalse(driver.find_elements(By.CSS_SELECTOR, '[role="alert"]'))

            walking = driver.find_element(By.CSS_SELECTOR, '.walking')
            self.assertEqual(walking.find_element(By.TAG_NAME, 'h2').text, 'Walking and recovery')
            self.assertIn('not proof of recovery', walking.text)
            self.assertIn('does not earn fatigue credit', walking.text)
            refresh = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '.walking button')))
            refresh.click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.walking-item')))
            self.assertIn(walking.find_element(By.CSS_SELECTOR, '.walking-label').text,
                          ['Recovery-compatible', 'Additional activity load', 'Ordinary activity', 'Uncertain'])
            self.assertIn('Why this assessment', walking.text)
            self.assertIn('Duration:', walking.text)
            self.assertFalse(walking.find_elements(By.CSS_SELECTOR, 'input,select,textarea'))

            # Only replace the read-only request in this browser. Other API calls stay real.
            driver.execute_script("""
                window.walkingOriginalFetch = window.fetch;
                window.walkingRequests = 0;
                window.walkingResponse = {
                    metadata: {}, period: {start: '2026-01-02', end: '2026-01-02'},
                    summary: {recovery_compatible: 0, additional_load: 0,
                              ordinary_activity: 0, uncertain: 0,
                              previous_day_additional_load: false},
                    walks: [], policy: {}, limitations: ['Synthetic assessment limit.']
                };
                window.fetch = (url, options) => {
                    if (url !== '/api/walking/context') return window.walkingOriginalFetch(url, options);
                    window.walkingRequests += 1;
                    return new Promise(resolve => {
                        window.resolveWalking = () => resolve(new Response(
                            JSON.stringify(window.walkingResponse),
                            {status: window.walkingStatus || 200,
                             headers: {'Content-Type': 'application/json'}}));
                    });
                };
            """)
            refresh.click()
            wait.until(lambda d: d.execute_script('return window.walkingRequests') == 1)
            self.assertEqual(walking.get_attribute('aria-busy'), 'true')
            self.assertFalse(refresh.is_enabled())
            self.assertIn('Loading walking context', walking.text)
            driver.execute_script('window.resolveWalking()')
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, '.walking .empty'), 'No recorded walks'))
            self.assertIn('Synthetic assessment limit.', walking.text)
            self.assertEqual([node.text for node in walking.find_elements(By.CSS_SELECTOR, '.walking-summary dd')],
                             ['0', '0', '0', '0'])

            driver.execute_script("""
                window.walkingResponse.summary.uncertain = 1;
                window.walkingResponse.summary.previous_day_additional_load = true;
                window.walkingResponse.walks = [{
                    day: '2026-01-02', duration_minutes: null, intensity: null,
                    classification: 'uncertain',
                    reasons: ['Synthetic reason <img src=x onerror="window.walkingInjected=true">'],
                    data_gaps: ['Synthetic duration and intensity are missing.']
                }];
            """)
            refresh.click()
            wait.until(lambda d: d.execute_script('return window.walkingRequests') == 2)
            driver.execute_script('window.resolveWalking()')
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, '.walking-label'), 'Uncertain'))
            self.assertIn('2026-01-02', walking.text)
            self.assertIn('Duration: Not recorded', walking.text)
            self.assertIn('Intensity: Not recorded', walking.text)
            self.assertIn('Synthetic duration and intensity are missing.', walking.text)
            self.assertIn('previous day includes additional walking load', walking.text)
            self.assertIn('<img src=x', walking.text)
            self.assertFalse(walking.find_elements(By.TAG_NAME, 'img'))
            self.assertIsNone(driver.execute_script('return window.walkingInjected'))

            driver.execute_script('window.walkingStatus = 503')
            refresh.click()
            wait.until(lambda d: d.execute_script('return window.walkingRequests') == 3)
            driver.execute_script('window.resolveWalking()')
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, '.walking [role="alert"]'),
                                                      'Walking context is unavailable'))
            self.assertTrue(refresh.is_enabled())
            driver.execute_script('window.fetch = window.walkingOriginalFetch')
            refresh.click()
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.walking-item')))
            self.assertFalse(walking.find_elements(By.CSS_SELECTOR, '[role="alert"]'))
        finally:
            driver.quit()
