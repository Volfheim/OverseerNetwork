const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function editor() {
    const context = vm.createContext({
        window: { OVERSEER_SERVERS: [{id: 'existing'}], addEventListener() {} },
        t: key => key,
    });
    vm.runInContext(fs.readFileSync(require.resolve('../static/js/control_panel.js'), 'utf8'), context);
    return context;
}

test('a new draft cannot overwrite an existing node by reusing its ID', () => {
    const ui = editor();
    assert.throws(() => ui.saveRequest({id: 'existing'}, false), /duplicateNodeId/);
    assert.equal(ui.saveRequest({id: 'new_node'}, false).method, 'POST');
    assert.equal(ui.saveRequest({id: 'existing'}, true).method, 'PUT');
});

test('cancelling replacement preserves the current draft', async () => {
    const ui = editor();
    let fills = 0;
    ui.document = {getElementById: () => ({dataset: {dirty: 'true'}})};
    ui.window.overseerConfirm = async () => false;
    ui.fillForm = () => fills++;
    await ui.openEditor();
    assert.equal(fills, 0);
});

test('confirming replacement creates the requested node type', async () => {
    const ui = editor();
    const fields = {type: {}, name: {focus() {}}};
    let fills = 0;
    ui.document = {getElementById: () => ({dataset: {dirty: 'true'}})};
    ui.window.overseerConfirm = async () => true;
    ui.fillForm = () => fills++;
    ui.formField = name => fields[name];
    ui.updateGeoMode = () => {};
    ui.renderOverseerControlPanel = () => {};
    ui.switchPanel = name => assert.equal(name, 'editor');
    await ui.openEditor(null, 'local');
    assert.equal(fills, 1);
    assert.equal(fields.type.value, 'local');
});

test('opening the same node does not reset unsaved edits', async () => {
    const ui = editor();
    ui.formField = name => name === 'id' ? {disabled: true, value: 'existing'} : {focus() {}};
    ui.fillForm = () => assert.fail('draft replaced');
    ui.mayReplaceDraft = () => assert.fail('unnecessary discard prompt');
    ui.renderOverseerControlPanel = () => {};
    ui.switchPanel = name => assert.equal(name, 'editor');
    await ui.openEditor({id: 'existing'});
});

test('pending saves cannot be discarded or submitted twice', async () => {
    const ui = editor();
    vm.runInContext('editorBusy = true', ui);
    assert.equal(await ui.mayReplaceDraft(), false);
    ui.payloadFromForm = () => assert.fail('duplicate submission');
    await ui.saveServer({preventDefault() {}});
});

test('invalid fields open all collapsed ancestor sections before focus', () => {
    const ui = editor();
    const outer = {tagName: 'DETAILS', open: false};
    const inner = {tagName: 'DETAILS', open: false, parentElement: outer};
    let focused = false;
    ui.revealInvalidField({parentElement: inner, focus() { focused = true; }});
    assert.ok(inner.open && outer.open && focused);
});

test('validation arrays never render as object Object', () => {
    const ui = editor();
    assert.equal(ui.apiErrorMessage([{msg: 'Invalid port'}, {msg: 'Missing name'}]), 'Invalid port; Missing name');
    assert.equal(ui.apiErrorMessage(null), 'saveFailed');
});

test('local nodes disable hidden SSH inputs and require complete coordinate pairs', () => {
    const ui = editor();
    const fields = {type: {value: 'local'}, host: {}, username: {}, auto_geo: {checked: true}, lat: {value: '51.5'}, lon: {value: ''}};
    const ssh = [{}, {}], geo = [{}, {}];
    ui.formField = name => fields[name];
    ui.document = {
        getElementById: () => ({}), querySelector: () => ({}),
        querySelectorAll: selector => selector.includes('connection') ? ssh : geo,
    };
    ui.updateGeoMode();
    assert.ok(ssh.every(field => field.disabled));
    assert.ok(geo.every(field => !field.disabled));
    assert.ok(fields.lat.required && fields.lon.required);
    fields.type.value = 'ssh';
    ui.updateGeoMode();
    assert.ok(geo.every(field => field.disabled));
    assert.ok(!fields.lat.required && !fields.lon.required);
});

test('successful save clears the dirty guard before reload and restores the saved node', () => {
    const ui = editor();
    const steps = [];
    ui.sessionStorage = {setItem: (key, id) => steps.push(id)};
    ui.markEditorDirty = dirty => steps.push(dirty);
    ui.window.location = {reload: () => steps.push('reload')};
    ui.showSavedNode('new_node');
    assert.deepEqual(steps, ['new_node', false, 'reload']);
});
