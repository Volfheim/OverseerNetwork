const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function preferences(saved) {
    const tokens = new Set();
    const context = vm.createContext({
        localStorage: { getItem: () => JSON.stringify(saved), setItem() {} },
        document: { body: { classList: { toggle: (key, value) => value ? tokens.add(key) : tokens.delete(key) } },
            documentElement: { classList: { toggle() {} } } },
        CustomEvent: class {}, window: { dispatchEvent() {} },
    });
    vm.runInContext(fs.readFileSync(require.resolve('../static/js/preferences.js'), 'utf8'), context);
    return { prefs: context.window.OVERSEER_PREFS, context, tokens };
}
test('Fallout is the default theme, with tactical Earth and space enabled', () => {
    const { prefs, tokens } = preferences({});
    assert.equal(prefs.uiTheme, 'fallout');
    assert.equal(prefs.mapSurface, 'tactical');
    assert.equal(prefs.spaceBackground, true);
    assert.ok(tokens.has('theme-fallout'));
});
test('existing display preferences migrate without losing language or power settings', () => {
    const { prefs } = preferences({ language: 'en', lowPower: true, mapSurface: 'relief' });
    assert.equal(prefs.uiTheme, 'fallout');
    assert.equal(prefs.mapSurface, 'tactical');
    assert.equal(prefs.language, 'en');
    assert.equal(prefs.lowPower, true);
});
test('the operations theme remains an explicit persistent choice', () => {
    const { prefs, tokens } = preferences({ uiTheme: 'operations', mapSurface: 'relief', spaceBackground: false });
    assert.equal(prefs.uiTheme, 'operations');
    assert.equal(prefs.mapSurface, 'relief');
    assert.equal(prefs.spaceBackground, false);
    assert.ok(!tokens.has('theme-fallout'));
});
