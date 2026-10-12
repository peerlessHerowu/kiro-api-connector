import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js required for bridge conversion')
class CreateResponseTests(unittest.TestCase):
    def test_tool_round_trip_and_effort(self):
        module = Path(__file__).parents[1] / 'runtime' / 'create_response.cjs'
        script = r"""
const assert=require('node:assert/strict');
const {chatRequest}=require(process.argv[1]);
const config={models:['test'],defaultModel:'test',prefix:'isolated',effortModels:['test']};
const {payload}=chatRequest({model:'test',instructions:'Be accurate',reasoning:{effort:'high'},
  input:[{role:'user',content:'Find x'},
    {type:'function_call',call_id:'call_1',name:'lookup',arguments:'{"query":"x"}'},
    {type:'function_call_output',call_id:'call_1',output:'found'},
    {role:'assistant',content:'Done'}],
  tools:[{type:'function',name:'lookup',parameters:{type:'object',properties:{}}}]},config);
assert.equal(payload.model,'isolated/test');
assert.equal(payload.reasoning_effort,'high');
assert.equal(payload.messages[2].tool_calls[0].function.name,'lookup');
assert.deepEqual(payload.messages[3],{role:'tool',tool_call_id:'call_1',content:'found'});
assert.equal(payload.tools[0].function.name,'lookup');
assert.throws(()=>chatRequest({model:'test',input:[{role:'user',content:[{type:'input_file'}]}]},config),
  /Unsupported Responses content type/);
"""
        result = subprocess.run(['node', '-e', script, str(module)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
