import sys
import os
import unittest

# Add paths
sys.path.append(os.path.abspath("C:/Users/HP/.gemini/antigravity/scratch/condominium_manager"))
sys.path.append(os.path.abspath("C:/Users/HP/.gemini/antigravity/brain/4dd0e7fb-41d2-4c6a-8e05-58c8f6c94861/scratch"))

from test_meta_webhook import TestMetaWebhook

if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMetaWebhook)
    
    # Open file for writing results
    with open("C:/Users/HP/.gemini/antigravity/scratch/condominium_manager/test_out.txt", "w", encoding="utf-8") as f:
        f.write("=== RUNNING META WEBHOOK TESTS ===\n")
        runner = unittest.TextTestRunner(stream=f, verbosity=2)
        result = runner.run(suite)
        f.write(f"\nTests run: {result.testsRun}\n")
        f.write(f"Errors: {len(result.errors)}\n")
        f.write(f"Failures: {len(result.failures)}\n")
        if result.wasSuccessful():
            f.write("\nSUCCESS: All tests passed!\n")
        else:
            f.write("\nFAILURE: Some tests failed.\n")
            for test, err in result.errors:
                f.write(f"\nERROR in {test}:\n{err}\n")
            for test, fail in result.failures:
                f.write(f"\nFAILURE in {test}:\n{fail}\n")

    print("Tests execution finished and logged to test_out.txt.")
