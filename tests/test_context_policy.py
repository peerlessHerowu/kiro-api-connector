import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js required for bridge policy')
class ContextPolicyTests(unittest.TestCase):
    def test_budget_and_large_tool_result(self):
        module = Path(__file__).parents[1] / 'runtime' / 'context_policy.cjs'
        script = """
const p=require(process.argv[1]);
if(p.budget({contextBudget:10000},'x')!==10000) throw Error('budget');
const messages=[{role:'tool',content:'x'.repeat(1000000)}];
if(p.protectMessages(messages,200000)!==1 || messages[0].content.length>=1000000) throw Error('clip');
if(p.usage({contextBudget:200000,inputEstimate:150000,contextClipped:1})<85) throw Error('usage');
"""
        result = subprocess.run(['node', '-e', script, str(module)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
