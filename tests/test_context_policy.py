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
const summary=[{role:'user',content:'x'.repeat(6000000)}];
if(p.protectMessages(summary,200000)!==1 || p.estimate(summary)>155000) throw Error('summary clip');
const mixed=[{role:'user',content:'[SYSTEM NOTE: automated summarization request]\\n'+'x'.repeat(1200000)+'中'.repeat(500000)+'\\nlatest user request'}];
p.protectMessages(mixed,200000);
if(p.estimate(mixed)>150010 || !mixed[0].content.startsWith('[SYSTEM NOTE: automated summarization request]') || !mixed[0].content.endsWith('latest user request')) throw Error('mixed density / endpoints');
if(!mixed.connectorSummaryClipped || p.usage({contextBudget:200000,inputEstimate:150000,contextClipped:1,summaryClipped:true})>=80) throw Error('summary loop');
const ordinary=[{role:'user',content:'x'.repeat(480000)}];
if(p.protectMessages(ordinary,200000)!==0) throw Error('premature clipping');
if(p.usage({contextBudget:200000,inputEstimate:150000,contextClipped:1})<85) throw Error('usage');
"""
        result = subprocess.run(['node', '-e', script, str(module)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
