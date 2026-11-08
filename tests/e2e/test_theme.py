"""Theme behavior in a fresh browser against the isolated synthetic dashboard."""
import os
import unittest

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select, WebDriverWait


class ThemeBrowserTests(unittest.TestCase):
    def test_system_preference_explicit_choice_and_reload(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--window-size=1440,1100')
        driver = webdriver.Remote(command_executor=os.environ['SELENIUM_URL'], options=options)
        try:
            wait = WebDriverWait(driver, 25)

            def system(theme):
                driver.execute_cdp_cmd('Emulation.setEmulatedMedia', {
                    'features': [{'name': 'prefers-color-scheme', 'value': theme}]
                })

            def themed(theme, preference):
                wait.until(lambda d: d.find_elements(By.ID, 'theme-choice'))
                wait.until(lambda d: d.execute_script('''
                    return document.documentElement.dataset.theme === arguments[0]
                        && document.documentElement.style.colorScheme === arguments[0];
                ''', theme))
                self.assertEqual(Select(driver.find_element(By.ID, 'theme-choice'))
                                 .first_selected_option.get_attribute('value'), preference)

            # Capture the early theme before the module renders any dashboard content.
            driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': '''
                document.addEventListener('DOMContentLoaded', () => {
                    window.initialTheme = document.documentElement.dataset.theme;
                }, {once: true});
            '''})
            system('dark')
            driver.get(os.environ['TEST_WEB_URL'])
            wait.until(lambda d: d.find_elements(By.ID, 'theme-choice'))
            themed('dark', 'system')
            self.assertEqual(driver.execute_script('return window.initialTheme'), 'dark')
            self.assertIsNone(driver.execute_script(
                "return localStorage.getItem('personal-coach-theme')"))
            self.assertEqual(driver.find_element(By.ID, 'theme-choice').accessible_name, 'Appearance')

            # Keyboard focus and native arrow-key selection work without a pointer.
            choice = driver.find_element(By.ID, 'theme-choice')
            choice.send_keys(Keys.HOME, Keys.ARROW_DOWN, Keys.ENTER)
            themed('light', 'light')
            self.assertEqual(driver.execute_script('return document.activeElement.id'), 'theme-choice')
            self.assertEqual(driver.execute_script(
                "return localStorage.getItem('personal-coach-theme')"), 'light')
            driver.refresh()
            themed('light', 'light')
            self.assertEqual(driver.execute_script('return window.initialTheme'), 'light')
            system('light')
            system('dark')
            themed('light', 'light')

            Select(driver.find_element(By.ID, 'theme-choice')).select_by_value('dark')
            themed('dark', 'dark')
            system('light')
            themed('dark', 'dark')
            driver.refresh()
            themed('dark', 'dark')
            self.assertEqual(driver.execute_script('return window.initialTheme'), 'dark')

            Select(driver.find_element(By.ID, 'theme-choice')).select_by_value('system')
            themed('light', 'system')
            system('dark')
            themed('dark', 'system')
            driver.refresh()
            themed('dark', 'system')
            system('light')
            themed('light', 'system')

            # Unknown browser values fall back to the current system theme.
            driver.execute_script("localStorage.setItem('personal-coach-theme', 'invalid')")
            system('dark')
            driver.refresh()
            themed('dark', 'system')
            self.assertEqual(driver.execute_script('return window.initialTheme'), 'dark')
        finally:
            driver.quit()
