const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = (name) => JSON.parse(fs.readFileSync(path.join(__dirname, name), 'utf8'));
const main = read('leadpilot-post-call.json');
const errors = read('leadpilot-error-handler.json');
const retell = read('LeadPilot AI - Retell + n8n v0.json');
const get = (workflow, name) => workflow.nodes.find(node => node.name === name);
const run = (code, input, lookups = {}) => vm.runInNewContext(`(function(){${code}\n})()`, {
  $input: { first: () => ({ json: input }), all: () => input.map(json => ({ json })) },
  $: (name) => ({ first: () => ({ json: lookups[name] }) }),
});

for (const workflow of [main, errors]) {
  assert.equal(workflow.active, false);
  const names = new Set(workflow.nodes.map(node => node.name));
  assert.equal(names.size, workflow.nodes.length);
  for (const [source, connection] of Object.entries(workflow.connections)) {
    assert(names.has(source), source);
    for (const output of connection.main) {
      for (const edge of output) assert(names.has(edge.node), edge.node);
    }
  }
  for (const node of workflow.nodes.filter(node => node.type === 'n8n-nodes-base.code')) {
    new Function(node.parameters.jsCode);
  }
}

assert.equal(get(main, 'Retell Call End Webhook').parameters.path, 'retell-call-end');
assert(get(errors, 'Error Trigger'));
assert(!get(main, 'Error Trigger'));

const parse = get(main, 'Parse Call Data').parameters.jsCode;
const sample = {
  event: 'call_analyzed', call: {
    call_id: 'call_001', call_status: 'ended', from_number: '+15551234567',
    to_number: '+15557654321', transcript_object: [
      { role: 'agent', content: 'How can I help?' },
      { role: 'user', content: 'My name is Sarah Johnson. I have a burst pipe at 42 Maple Street.' },
    ], call_analysis: { summary: 'Burst pipe at home.', in_voicemail: false },
  },
};
const parsed = run(parse, { body: sample })[0].json;
assert.equal(parsed.caller_name, 'Sarah Johnson');
assert.equal(parsed.urgency, 'emergency');
assert.equal(parsed.service_type, 'Service not specified');
assert.equal(parsed.address, '42 Maple Street');
assert.equal(run(parse, { body: { ...sample, event: 'call_ended' } }).length, 0);
assert.equal(run(parse, { body: { ...sample.call, call_id: 'flat_001' } })[0].json.call_id, 'flat_001');
const unclear = structuredClone(sample);
unclear.call.transcript_object[1].content = 'I need help soon.';
assert.equal(run(parse, { body: unclear })[0].json.urgency, 'needs_review');

const outOfArea = structuredClone(sample);
outOfArea.call.transcript_object[1].content = 'We are outside your service area.';
assert.equal(run(parse, { body: outOfArea })[0].json.urgency, 'out_of_area');

const check = get(main, 'Decide Duplicate').parameters.jsCode;
const recent = new Date().toISOString();
assert.equal(run(check, [{ call_id: 'other', from_number: parsed.from_number, created_at: recent }],
  { 'Parse Call Data': parsed })[0].json.duplicate, true);
assert.equal(run(check, [{}], { 'Parse Call Data': parsed })[0].json.duplicate, false);

const clientCheck = get(main, 'Assert Jobber Client').parameters.jsCode;
assert.throws(() => run(clientCheck, { data: { clientCreate: { userErrors: [{ message: 'Missing name' }] } } }), /Jobber clientCreate failed/);
assert.equal(run(clientCheck, { data: { clientCreate: { client: { id: 'abc' }, userErrors: [] } } })[0].json.client_id, 'abc');

console.log('LeadPilot workflows: structure, parsing, deduplication, and Jobber checks passed');

assert.equal(retell.is_published, false);
assert.equal(retell.conversationFlow.is_published, false);
assert.deepEqual(retell.webhook_events, ['call_analyzed']);
assert.equal(retell.webhook_url, 'https://n8n.sohaib.systems/webhook/retell-call-end');
const retellIds = new Set(retell.conversationFlow.nodes.map(node => node.id));
assert.equal(retellIds.size, retell.conversationFlow.nodes.length);
for (const node of retell.conversationFlow.nodes) {
  for (const route of node.edges ?? []) assert(retellIds.has(route.destination_node_id));
}
console.log('LeadPilot Retell agent: import structure and all conversation edges passed');
