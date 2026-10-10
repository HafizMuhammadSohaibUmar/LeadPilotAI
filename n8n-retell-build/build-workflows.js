const fs = require('node:fs');
const path = require('node:path');

const out = __dirname;
const expression = (value) => `={{ ${value} }}`;
const body = (fn) => fn.toString().slice(fn.toString().indexOf('{') + 1, -1).trim();
const node = (name, type, typeVersion, parameters, x, y, extra = {}) => ({
  id: name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), name,
  type: `n8n-nodes-base.${type}`, typeVersion, parameters, position: [x, y], ...extra,
});
const link = (target, index = 0) => ({ node: target, type: 'main', index });
const supabaseLog = (name, outcome, x, y) => node(name, 'supabase', 1, {
  resource: 'row', operation: 'create', tableId: 'retell_call_logs',
  dataToSend: 'defineBelow', fieldsUi: { fieldValues: [
    ['call_id', expression("$('Parse Call Data').first().json.call_id")],
    ['call_status', expression("$('Parse Call Data').first().json.call_status")],
    ['from_number', expression("$('Parse Call Data').first().json.from_number")],
    ['to_number', expression("$('Parse Call Data').first().json.to_number")],
    ['start_timestamp', expression("$('Parse Call Data').first().json.start_timestamp")],
    ['end_timestamp', expression("$('Parse Call Data').first().json.end_timestamp")],
    ['summary', expression("$('Parse Call Data').first().json.summary")],
    ['in_voicemail', expression("$('Parse Call Data').first().json.in_voicemail")],
    ['caller_name', expression("$('Parse Call Data').first().json.caller_name")],
    ['service_type', expression("$('Parse Call Data').first().json.service_type")],
    ['address', expression("$('Parse Call Data').first().json.address")],
    ['urgency', expression("$('Parse Call Data').first().json.urgency")],
    ['call_outcome', expression("$('Parse Call Data').first().json.call_outcome")],
    ['transcript', expression("$('Parse Call Data').first().json.transcript")],
    ['outcome', outcome],
    ['jobber_client_id', outcome === 'out_of_area' ? '' : expression("$('Assert Jobber Request').first().json.client_id")],
    ['jobber_request_id', outcome === 'out_of_area' ? '' : expression("$('Assert Jobber Request').first().json.request_id")],
  ].map(([fieldId, fieldValue]) => ({ fieldId, fieldValue })) },
}, x, y);

function parseCall() {
  const envelope = $input.first().json.body ?? $input.first().json;
  if (envelope.event && envelope.event !== 'call_analyzed') return [];
  const call = envelope.call ?? envelope;
  if (!call.call_id || !call.from_number) throw new Error('Retell call_id and from_number are required');
  const turns = Array.isArray(call.transcript_object) ? call.transcript_object : [];
  const callerText = turns.filter(t => t.role === 'user').map(t => String(t.content ?? '')).join(' ');
  const fullText = turns.map(t => String(t.content ?? '')).join(' ') || String(call.transcript ?? '');
  const analysis = call.call_analysis ?? {};
  const name = callerText.match(/\b(?:my name is|this is|i am|i'm)\s+([a-z]+(?:\s+[a-z]+)?)/i);
  const service = fullText.match(/\b(?:air conditioning|ac repair|hvac|furnace|heating|plumbing|drain|water heater|roofing|roof repair|roof inspection|electrical|garage door|pest control|tree removal)\b/i);
  const address = callerText.match(/\b\d{1,6}\s+[a-z0-9.\s]{3,50}?\s+(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd)\b/i);
  const outOfArea = /\b(?:outside (?:our|the|your) service area|we do not service (?:that|your) area|out of (?:our|the) service area)\b/i.test(fullText);
  const emergency = !/\b(?:not|no)\s+(?:an?\s+)?emergency\b/i.test(callerText) &&
    /\b(?:emergency|urgent|burst pipe|flooding|gas leak|gas odor|smell gas|sparking|smoke|fire|exposed live wiring|no power|no heat|no ac|no air conditioning)\b/i.test(callerText);
  const urgency = outOfArea ? 'out_of_area' : emergency ? 'emergency' :
    (!name || !service) ? 'needs_review' : 'qualified';
  return [{ json: {
    call_id: call.call_id, call_status: call.call_status ?? null,
    from_number: call.from_number, to_number: call.to_number ?? null,
    start_timestamp: call.start_timestamp ?? null, end_timestamp: call.end_timestamp ?? null,
    summary: analysis.summary ?? '', in_voicemail: Boolean(analysis.in_voicemail),
    caller_name: name?.[1]?.trim() ?? 'Unknown caller', service_type: service?.[0] ?? 'Service not specified',
    address: address?.[0]?.trim() ?? null, urgency, call_outcome: urgency,
    transcript: String(call.transcript ?? fullText),
  } }];
}

function checkDuplicates() {
  const call = $('Parse Call Data').first().json;
  const rows = $input.all().map(item => item.json).filter(item => item && item.call_id);
  const cutoff = Date.now() - 60 * 60 * 1000;
  const duplicate = rows.some(row => row.call_id === call.call_id ||
    (row.from_number === call.from_number && Date.parse(row.created_at) >= cutoff));
  return [{ json: { ...call, duplicate } }];
}

function checkJobberClient() {
  const response = $input.first().json;
  const errors = [...(response.errors ?? []), ...(response.data?.clientCreate?.userErrors ?? [])];
  if (errors.length || !response.data?.clientCreate?.client?.id) {
    throw new Error(`Jobber clientCreate failed: ${JSON.stringify(errors)}`);
  }
  return [{ json: { client_id: response.data.clientCreate.client.id } }];
}

function checkJobberRequest() {
  const response = $input.first().json;
  const errors = [...(response.errors ?? []), ...(response.data?.requestCreate?.userErrors ?? [])];
  if (errors.length || !response.data?.requestCreate?.request?.id) {
    throw new Error(`Jobber requestCreate failed: ${JSON.stringify(errors)}`);
  }
  return [{ json: {
    client_id: $('Assert Jobber Client').first().json.client_id,
    request_id: response.data.requestCreate.request.id,
    urgency: $('Parse Call Data').first().json.urgency,
  } }];
}

const jobberHeaders = { parameters: [
  { name: 'X-JOBBER-GRAPHQL-VERSION', value: '2026-09-25' },
  { name: 'Content-Type', value: 'application/json' },
] };
const jobber = (name, query, variables, x, y) => node(name, 'httpRequest', 4.2, {
  method: 'POST', url: 'https://api.getjobber.com/api/graphql',
  authentication: 'genericCredentialType', genericAuthType: 'oAuth2Api',
  sendHeaders: true, headerParameters: jobberHeaders,
  sendBody: true, specifyBody: 'json',
  jsonBody: expression(`JSON.stringify({query: ${JSON.stringify(query)}, variables: ${variables}})`),
  options: { timeout: 15000 },
}, x, y);
const sms = (name, to, message, x, y) => node(name, 'twilio', 1, {
  resource: 'sms', operation: 'send', from: 'REPLACE_WITH_TWILIO_E164',
  to, message,
}, x, y);

const main = {
  id: 'LpRtMain20261010',
  name: 'LeadPilot Retell - Post-call (inactive until verified)',
  nodes: [
    node('Setup Notes', 'stickyNote', 1, { content:
      '## Setup before activation\nCredentials: Twilio, Jobber OAuth2 (or client-approved Housecall Pro alternative), Supabase, Slack (in separate error workflow). Configure owner/Twilio E.164 numbers, schema.sql, and Error workflow setting. This webhook MUST have verified Retell raw-body signatures at ingress before activation.\n\nCost: Retell per-minute and Twilio telephony/SMS are billed to the client retainer, not the developer. This post-call workflow has no LLM or voice cost of its own.', width: 520, height: 320 }, -240, -420),
    node('Retell Call End Webhook', 'webhook', 1, { httpMethod: 'POST', path: 'retell-call-end', responseMode: 'onReceived', options: {} }, 0, 0),
    node('Parse Call Data', 'code', 2, { jsCode: body(parseCall) }, 220, 0),
    node('Duplicate Check (Supabase)', 'supabase', 1, {
      resource: 'row', operation: 'getAll', tableId: 'retell_call_logs', returnAll: true,
      filters: { conditions: [
        { keyName: 'from_number', condition: 'eq', keyValue: expression("$('Parse Call Data').first().json.from_number") },
        { keyName: 'created_at', condition: 'gte', keyValue: expression('$now.minus({ minutes: 60 }).toISO()') },
      ] }, options: {},
    }, 440, 0, { alwaysOutputData: true }),
    node('Decide Duplicate', 'code', 2, { jsCode: body(checkDuplicates) }, 660, 0),
    node('Is Duplicate?', 'if', 2, { conditions: { options: { caseSensitive: true, typeValidation: 'strict' }, conditions: [
      { leftValue: expression('$json.duplicate'), rightValue: true, operator: { type: 'boolean', operation: 'equal' } },
    ] }, options: {} }, 880, 0),
    node('Urgency Router', 'switch', 3, { mode: 'expression', numberOutputs: 4,
      output: expression("$json.urgency === 'emergency' ? 0 : ($json.urgency === 'qualified' ? 1 : ($json.urgency === 'out_of_area' ? 2 : 3))"),
      options: {} }, 1100, 120),
    sms('SMS Owner - Emergency', 'REPLACE_WITH_OWNER_E164',
      expression("'EMERGENCY CALL: ' + $('Parse Call Data').first().json.caller_name + ' (' + $('Parse Call Data').first().json.from_number + ') - ' + $('Parse Call Data').first().json.service_type + '. ' + $('Parse Call Data').first().json.summary"), 1320, -100),
    jobber('Jobber Create Client',
      'mutation CreateClient($input: ClientCreateInput!) { clientCreate(input: $input) { client { id } userErrors { message } } }',
      "{input:{firstName: $('Parse Call Data').first().json.caller_name.split(' ')[0], lastName: $('Parse Call Data').first().json.caller_name.split(' ').slice(1).join(' ') || 'Caller', phones:[{number:$('Parse Call Data').first().json.from_number, primary:true}]}}",
      1320, 220),
    node('Assert Jobber Client', 'code', 2, { jsCode: body(checkJobberClient) }, 1540, 220),
    jobber('Jobber Create Request',
      'mutation CreateRequest($input: RequestCreateInput!) { requestCreate(input: $input) { request { id } userErrors { message } } }',
      "{input:{clientId: $json.client_id, title: ($('Parse Call Data').first().json.urgency === 'emergency' ? 'HIGH PRIORITY - ' : '') + $('Parse Call Data').first().json.service_type, instructions: ($('Parse Call Data').first().json.summary || 'Follow up with caller') + ' | Address: ' + ($('Parse Call Data').first().json.address || 'Not provided') + ' | Call ID: ' + $('Parse Call Data').first().json.call_id}}",
      1760, 220),
    node('Assert Jobber Request', 'code', 2, { jsCode: body(checkJobberRequest) }, 1980, 220),
    node('Created Lead Router', 'switch', 3, { mode: 'expression', numberOutputs: 2,
      output: expression("$json.urgency === 'emergency' ? 0 : 1"), options: {} }, 2200, 220),
    supabaseLog('Log Emergency (Supabase)', 'emergency', 2420, 120),
    supabaseLog('Log Qualified (Supabase)', 'qualified', 2420, 360),
    sms('SMS Confirm Caller', expression("$('Parse Call Data').first().json.from_number"),
      expression("'Thanks for calling. We received your request and someone will follow up shortly. No appointment has been confirmed.'"), 2640, 360),
    sms('SMS Lead Summary to Owner', 'REPLACE_WITH_OWNER_E164',
      expression("'NEW LEAD: ' + $('Parse Call Data').first().json.caller_name + ' (' + $('Parse Call Data').first().json.from_number + ') - ' + $('Parse Call Data').first().json.service_type + '. ' + $('Parse Call Data').first().json.summary"), 2860, 360),
    supabaseLog('Log Out of Area (Supabase)', 'out_of_area', 1320, 520),
    supabaseLog('Log Needs Review (Supabase)', 'needs_review', 1320, 720),
    sms('SMS Owner - Review Needed', 'REPLACE_WITH_OWNER_E164',
      expression("'REVIEW CALL: ' + $('Parse Call Data').first().json.from_number + '. ' + $('Parse Call Data').first().json.summary"), 1540, 720),
  ],
  connections: {
    'Retell Call End Webhook': { main: [[link('Parse Call Data')]] },
    'Parse Call Data': { main: [[link('Duplicate Check (Supabase)')]] },
    'Duplicate Check (Supabase)': { main: [[link('Decide Duplicate')]] },
    'Decide Duplicate': { main: [[link('Is Duplicate?')]] },
    'Is Duplicate?': { main: [[], [link('Urgency Router')]] },
    'Urgency Router': { main: [[link('SMS Owner - Emergency'), link('Jobber Create Client')],
      [link('Jobber Create Client')], [link('Log Out of Area (Supabase)')], [link('Log Needs Review (Supabase)')]] },
    'Jobber Create Client': { main: [[link('Assert Jobber Client')]] },
    'Assert Jobber Client': { main: [[link('Jobber Create Request')]] },
    'Jobber Create Request': { main: [[link('Assert Jobber Request')]] },
    'Assert Jobber Request': { main: [[link('Created Lead Router')]] },
    'Created Lead Router': { main: [[link('Log Emergency (Supabase)')], [link('Log Qualified (Supabase)')]] },
    'Log Qualified (Supabase)': { main: [[link('SMS Confirm Caller')]] },
    'Log Needs Review (Supabase)': { main: [[link('SMS Owner - Review Needed')]] },
    'SMS Confirm Caller': { main: [[link('SMS Lead Summary to Owner')]] },
  },
  active: false, settings: { executionOrder: 'v1', errorWorkflow: '' }, staticData: null,
};

const errors = {
  id: 'LpRtErr20261010A',
  name: 'LeadPilot Retell - Error Handler',
  nodes: [
    node('Setup Notes', 'stickyNote', 1, { content:
      '## Error workflow\nConfigure Supabase and Slack credentials, plus a real Slack channel. Save this workflow and select it under the main workflow Settings > Error workflow. Do not use the main workflow as its own handler. Retell and Twilio costs are client-billed; this error workflow has no LLM/voice cost.', width: 500, height: 250 }, -160, -300),
    node('Error Trigger', 'errorTrigger', 1, {}, 0, 0),
    node('Log Error (Supabase)', 'supabase', 1, { resource: 'row', operation: 'create', tableId: 'retell_call_errors',
      dataToSend: 'defineBelow', fieldsUi: { fieldValues: [
        ['workflow_name', expression('$json.workflow?.name ?? "unknown"')],
        ['node_name', expression('$json.execution?.lastNodeExecuted ?? $json.trigger?.error?.node?.name ?? "trigger"')],
        ['error_message', expression('$json.execution?.error?.message ?? $json.trigger?.error?.message ?? "unknown error"')],
        ['execution_id', expression('$json.execution?.id ?? ""')],
      ].map(([fieldId, fieldValue]) => ({ fieldId, fieldValue })) },
    }, 220, -100),
    node('Alert Owner (Slack)', 'slack', 2, { resource: 'message', operation: 'post', channel: 'REPLACE_WITH_SLACK_CHANNEL',
      text: expression("'LeadPilot post-call failure in ' + ($json.workflow?.name ?? 'unknown') + ' at ' + ($json.execution?.lastNodeExecuted ?? 'trigger') + ': ' + ($json.execution?.error?.message ?? $json.trigger?.error?.message ?? 'unknown error')"),
    }, 220, 120),
  ],
  connections: { 'Error Trigger': { main: [[link('Log Error (Supabase)'), link('Alert Owner (Slack)')]] } },
  active: false, settings: { executionOrder: 'v1' }, staticData: null,
};

fs.writeFileSync(path.join(out, 'leadpilot-post-call.json'), JSON.stringify(main, null, 2) + '\n');
fs.writeFileSync(path.join(out, 'leadpilot-error-handler.json'), JSON.stringify(errors, null, 2) + '\n');
