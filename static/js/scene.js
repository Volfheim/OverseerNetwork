(function () {
    'use strict';
    const container = document.getElementById('globe-container');
    const labelLayer = document.getElementById('map-label-layer');
    const alertBox = document.getElementById('map-alert');
    const popover = document.getElementById('map-site-popover');
    const servers = window.OVERSEER_SERVERS || [];
    let sites = OverseerMapGeometry.groupSites(servers);
    let viewInteracted = false;
    const byId = new Map(servers.map(node => [node.id, node]));
    const prefs = () => window.OVERSEER_PREFS || {};
    const tr = (key, values) => window.t(key, values);
    let errorKey = 'mapLoading';
    let needsRender = true;
    function showMapMessage(key) {
        errorKey = key;
        alertBox.hidden = !key;
        alertBox.textContent = key ? tr(key) : '';
    }

    // Status collection is independent of WebGL so the panel survives GPU failure.
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const statusSocket = new WebSocket(protocol + '//' + location.host + '/ws/status');
    statusSocket.onmessage = event => {
        try {
            window.OVERSEER_STATUSES = JSON.parse(event.data);
            window.applyOverseerStatuses?.();
            window.renderOverseerControlPanel?.();
        } catch (error) { console.error('Status parse error:', error); }
    };

    let renderer;
    try {
        renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'high-performance' });
    } catch {
        showMapMessage('mapGraphicsError');
        return;
    }
    container.appendChild(renderer.domElement);
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.setAttribute('aria-label', tr('mapCanvas'));
    renderer.domElement.addEventListener('webglcontextlost', event => {
        event.preventDefault();
        showMapMessage('mapGraphicsError');
    });
    renderer.domElement.addEventListener('webglcontextrestored', () => { needsRender = true; showMapMessage(''); });
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 1500);
    const space = window.createOverseerSpace(THREE);
    scene.add(space.points);
    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = false;
    controls.minDistance = 165;
    controls.maxDistance = 850;
    controls.minPolarAngle = 0.08;
    controls.maxPolarAngle = Math.PI - 0.08;
    controls.rotateSpeed = 0.55;
    controls.zoomSpeed = 0.7;
    controls.addEventListener('change', () => { needsRender = true; });

    const composer = new THREE.EffectComposer(renderer);
    composer.addPass(new THREE.RenderPass(scene, camera));
    const bloom = new THREE.UnrealBloomPass(new THREE.Vector2(1, 1), 0.18, 0.25, 0.9);
    composer.addPass(bloom);
    let useBloom = !prefs().lowPower;
    let loadedTextures = 0;
    let textureFailed = false;
    const loader = new THREE.TextureLoader();
    showMapMessage('mapLoading');
    function texture(path) {
        const result = loader.load(path, () => {
            needsRender = true;
            loadedTextures++;
            if (loadedTextures === 3 && !textureFailed) {
                container.dataset.surfaceReady = 'true';
                showMapMessage('');
            }
        }, undefined, () => {
            textureFailed = true;
            showMapMessage('mapTextureError');
        });
        result.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
        return result;
    }
    const earthMaterial = new THREE.ShaderMaterial({
        extensions: { derivatives: true },
        uniforms: {
            surface: { value: texture('/static/img/three-globe/earth-blue-marble.jpg') },
            elevation: { value: texture('/static/img/three-globe/earth-topology.png') },
            water: { value: texture('/static/img/three-globe/earth-water.png') },
            tactical: { value: 0 }, grid: { value: 1 },
            signalGain: { value: 1 },
            accent: { value: new THREE.Color('#a7cb8b') },
        },
        vertexShader: [
            'varying vec2 vUv; varying vec3 vNormal;',
            'void main() { vUv = uv; vNormal = normalize(normalMatrix * normal);',
            'gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
        ].join('\n'),
        fragmentShader: [
            'uniform sampler2D surface; uniform sampler2D elevation; uniform sampler2D water;',
            'uniform float tactical; uniform float grid; uniform vec3 accent; uniform float signalGain;',
            'varying vec2 vUv; varying vec3 vNormal;',
            'void main() {',
            'vec2 texel = vec2(1.0 / 2048.0, 1.0 / 1024.0);',
            'float sea = texture2D(water, vUv).r;',
            'float land = 1.0 - smoothstep(0.35, 0.65, sea);',
            'float h = texture2D(elevation, vUv).r;',
            'float dx = texture2D(elevation, vUv + vec2(texel.x, 0.0)).r - h;',
            'float dy = texture2D(elevation, vUv + vec2(0.0, texel.y)).r - h;',
            'float relief = clamp(1.0 + (dy - dx) * 6.0, 0.65, 1.45);',
            'float light = 0.58 + 0.42 * max(0.0, dot(normalize(vNormal), normalize(vec3(-0.4, 0.65, 1.0))));',
            'vec3 natural = texture2D(surface, vUv).rgb;',
            'natural = mix(vec3(0.035, 0.105, 0.135), natural * 1.12, land);',
            'vec3 phosphor = mix(vec3(0.025, 0.067, 0.073), accent * (0.27 + h * 0.58) * signalGain, land);',
            'vec3 color = mix(natural, phosphor, tactical) * relief * light;',
            'float coast = max(abs(sea - texture2D(water, vUv + vec2(texel.x, 0.0)).r),',
            'abs(sea - texture2D(water, vUv + vec2(0.0, texel.y)).r));',
            'color += coast * accent * mix(0.07, 0.34, tactical);',
            'vec2 coordinates = vUv * vec2(12.0, 12.0);',
            'vec2 distanceToLine = abs(fract(coordinates - 0.5) - 0.5) / max(fwidth(coordinates), vec2(0.00001));',
            'float line = 1.0 - min(min(distanceToLine.x, distanceToLine.y), 1.0);',
            'color = mix(color, mix(vec3(0.28, 0.48, 0.48), accent * 0.55, tactical), line * grid * 0.3);',
            'gl_FragColor = vec4(color, 1.0); }',
        ].join('\n'),
    });
    const globe = new THREE.Group();
    scene.add(globe);
    const earth = new THREE.Mesh(new THREE.SphereGeometry(100, 128, 96), earthMaterial);
    globe.add(earth);
    globe.add(new THREE.Mesh(new THREE.SphereGeometry(102.2, 64, 48), new THREE.ShaderMaterial({
        transparent: true, side: THREE.BackSide, depthWrite: false,
        vertexShader: 'varying vec3 n; varying vec3 p; void main() { n = normalize(normalMatrix * normal); vec4 view = modelViewMatrix * vec4(position, 1.0); p = view.xyz; gl_Position = projectionMatrix * view; }',
        fragmentShader: 'varying vec3 n; varying vec3 p; void main() { float rim = pow(1.0 - abs(dot(normalize(n), normalize(-p))), 3.0); gl_FragColor = vec4(0.3, 0.6, 0.65, rim * 0.22); }',
    })));
    const links = new THREE.Group();
    globe.add(links);
    function rebuildLinks() {
        links.children.forEach(line => { line.geometry.dispose(); line.material.dispose(); });
        links.clear();
        servers.forEach(node => {
            const parent = byId.get(node.upstream_id);
            if (!parent || !sites.some(site => site.nodes.includes(node)) || !sites.some(site => site.nodes.includes(parent))) return;
            const points = OverseerMapGeometry.arcPoints(node.coordinates, parent.coordinates).map(point => new THREE.Vector3(...point));
            const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineDashedMaterial({
                color: 0x8fd5db, dashSize: 2.2, gapSize: 1.3, transparent: true, opacity: 0.8,
            }));
            line.computeLineDistances();
            links.add(line);
        });
    }
    rebuildLinks();

    let selectedId = servers[0]?.id || null;
    let openSite = null;
    let rotating = Boolean(prefs().mapRotate);
    let target = null;
    let zoomRatio = 1;
    let bounds = { width: 1, height: 1, left: 0, top: 0 };
    let labelObstacles = [];
    const home = new THREE.Vector3();
    function updateHome() {
        home.set(0, 0, 0);
        sites.forEach(site => home.add(new THREE.Vector3(...OverseerMapGeometry.position(site.coordinates))));
        if (home.length() < 0.1) home.set(0, 0.3, 1);
        home.normalize();
        const homeView = new THREE.Spherical().setFromVector3(home);
        homeView.phi = THREE.MathUtils.clamp(homeView.phi, THREE.MathUtils.degToRad(55), THREE.MathUtils.degToRad(125));
        home.setFromSpherical(homeView);
    }
    updateHome();
    camera.position.copy(home).multiplyScalar(340);

    const healthColors = { healthy: 0xa2d492, degraded: 0xe9bc69, offline: 0xec7d79, unknown: 0x90bac7 };
    function health(node) {
        const observation = window.OVERSEER_FLEET?.nodes.find(item => item.id === node.id);
        if (observation) {
            const age = Date.now() - Date.parse(observation.checked_at || '');
            return Number.isFinite(age) && age < 60000 ? observation.health : 'unknown';
        }
        const status = window.OVERSEER_STATUSES?.[node.id]?.status;
        return status === 'OFFLINE' ? 'offline' : 'unknown';
    }
    function siteHealth(site) {
        const values = site.nodes.map(health);
        return ['offline', 'degraded', 'unknown', 'healthy'].find(value => values.includes(value)) || 'unknown';
    }
    function healthText(value) { return tr(value === 'offline' ? 'offline' : 'health_' + value); }
    function city(site) {
        const name = site.nodes[0].city;
        return name === 'Moscow' ? tr('mapMoscow') : name === 'New York' ? tr('mapNewYork') : name || site.nodes[0].name;
    }
    const leaderCanvas = document.createElement('canvas');
    leaderCanvas.setAttribute('aria-hidden', 'true');
    labelLayer.appendChild(leaderCanvas);
    const leaders = leaderCanvas.getContext('2d');
    function createSiteMarkers() {
        sites.forEach(site => {
            site.marker = new THREE.Mesh(new THREE.SphereGeometry(1.35, 16, 12), new THREE.MeshBasicMaterial({ color: healthColors.unknown }));
            site.marker.position.set(...OverseerMapGeometry.position(site.coordinates, 101.6));
            site.marker.userData.site = site;
            const ring = new THREE.Mesh(new THREE.RingGeometry(2.2, 2.65, 32), new THREE.MeshBasicMaterial({
                color: healthColors.unknown, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false,
            }));
            ring.lookAt(site.marker.position);
            site.marker.add(ring);
            site.ring = ring;
            globe.add(site.marker);
            site.label = document.createElement('button');
            site.label.className = 'map-label';
            site.label.type = 'button';
            site.label.dataset.siteId = site.key;
            site.label.setAttribute('aria-expanded', 'false');
            site.label.innerHTML = '<strong></strong><span></span>';
            site.label.addEventListener('click', () => openLocation(site));
            labelLayer.appendChild(site.label);
        });
    }
    createSiteMarkers();

    function refreshLabels() {
        needsRender = true;
        sites.forEach(site => {
            const value = siteHealth(site);
            site.marker.material.color.setHex(healthColors[value] || healthColors.unknown);
            site.ring.material.color.copy(site.marker.material.color);
            site.label.className = 'map-label ' + value + (site.nodes.some(node => node.id === selectedId) ? ' selected' : '');
            site.label.querySelector('strong').textContent = site.nodes.length > 1 ? city(site) : site.nodes[0].name;
            site.label.querySelector('span').textContent = (site.nodes.length > 1 ? tr('mapSiteCount', { count: site.nodes.length }) : city(site)) + ' / ' + healthText(value);
            site.label.title = site.nodes.map(node => node.name).join(' / ');
            site.label.setAttribute('aria-expanded', String(site === openSite && !popover.hidden));
        });
        document.getElementById('map-inventory').textContent = tr('mapInventory', { nodes: servers.length, sites: sites.length });
        const unmapped = servers.filter(node => !node.coordinates).length;
        if (unmapped) document.getElementById('map-inventory').textContent += ' / ' + tr('geoUnmapped', { count: unmapped });
        if (openSite) renderSite();
    }

    function renderSite() {
        document.getElementById('site-title').textContent = city(openSite);
        const list = document.getElementById('site-nodes');
        // Preserve focused node buttons during the periodic health refresh.
        if (list.dataset.site !== openSite.key) {
            list.replaceChildren();
            list.dataset.site = openSite.key;
            openSite.nodes.forEach(node => {
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.nodeId = node.id;
                button.innerHTML = '<i class="health-dot"></i><span><strong></strong><small></small></span><em></em>';
                button.querySelector('strong').textContent = node.name;
                button.querySelector('small').textContent = (node.role || node.type) + ' / ' + (node.geo?.ip || node.host || 'LOCAL');
                button.addEventListener('click', () => selectNode(node.id, false));
                list.appendChild(button);
            });
        }
        list.querySelectorAll('button').forEach(button => {
            const value = health(byId.get(button.dataset.nodeId));
            button.querySelector('i').className = 'health-dot ' + value;
            button.querySelector('em').textContent = healthText(value);
            button.classList.toggle('selected', button.dataset.nodeId === selectedId);
            button.setAttribute('aria-pressed', String(button.dataset.nodeId === selectedId));
        });
        const sources = [...new Set(openSite.nodes.map(node => node.geo?.source || 'manual'))];
        const label = sources.includes('physical') ? tr('geoPhysical') : sources.includes('server-ip') ? tr('geoIpApprox') : tr('mapRegistryCoords');
        const stale = openSite.nodes.some(node => node.geo?.status === 'stale');
        document.getElementById('site-coordinates').textContent = openSite.coordinates.map(value => value.toFixed(4)).join(', ') + ' / ' + label + (stale ? ' / ' + tr('geoStale') : '');
    }
    function closeSite() {
        popover.hidden = true;
        openSite = null;
        sites.forEach(site => site.label.setAttribute('aria-expanded', 'false'));
    }
    function setRotation(value) {
        rotating = value;
        needsRender = true;
        document.querySelector('[data-map-action="rotate"]').setAttribute('aria-pressed', String(value));
    }
    function flyTo(direction, distance) {
        target = direction.clone().normalize().multiplyScalar(distance);
        zoomRatio = distance / fitDistance();
        setRotation(false);
    }
    function selectNode(id, focus = true) {
        viewInteracted = true;
        selectedId = id;
        window.selectedServerId = id;
        window.selectedServerName = byId.get(id)?.name;
        if (focus) {
            const site = sites.find(item => item.nodes.some(node => node.id === id));
            if (site) flyTo(site.marker.getWorldPosition(new THREE.Vector3()), fitDistance());
        }
        window.selectOverseerServer?.(id, false);
        refreshLabels();
    }
    function openLocation(site) {
        if (openSite === site) { closeSite(); return; }
        openSite = site;
        popover.hidden = false;
        selectNode(site.nodes.find(node => node.id === selectedId)?.id || site.nodes[0].id, false);
        setRotation(false);
        renderSite();
    }
    document.getElementById('close-site').addEventListener('click', () => {
        const label = openSite?.label;
        closeSite();
        label?.focus();
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') return;
        if (openSite) document.getElementById('close-site').click();
        else if (document.body.classList.contains('map-expanded')) document.querySelector('[data-map-action="expand"]').click();
    });

    const point = new THREE.Vector3();
    const projected = new THREE.Vector3();
    function updateLabels() {
        const candidates = [];
        const anchors = new Map();
        sites.forEach(site => {
            site.marker.getWorldPosition(point);
            const visible = prefs().mapLabels !== false && point.dot(camera.position) > 10000;
            projected.copy(point).project(camera);
            site.label.classList.add('hidden');
            if (!visible || Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1) return;
            site.label.classList.remove('hidden');
            const x = (projected.x * 0.5 + 0.5) * bounds.width;
            const y = (-projected.y * 0.5 + 0.5) * bounds.height;
            candidates.push({ id: site.key, x, y, width: site.label.offsetWidth, height: site.label.offsetHeight });
            anchors.set(site.key, { x, y });
        });
        const placed = OverseerMapGeometry.placeLabels(candidates, bounds, labelObstacles);
        leaders.clearRect(0, 0, bounds.width, bounds.height);
        leaders.lineWidth = 1;
        sites.forEach(site => {
            const rect = placed.find(item => item.id === site.key);
            if (!rect) { site.label.classList.add('hidden'); return; }
            site.label.style.transform = 'translate3d(' + rect.x + 'px, ' + rect.y + 'px, 0)';
            const anchor = anchors.get(site.key);
            leaders.strokeStyle = site.nodes.some(node => node.id === selectedId) ? '#e9bc6988' : '#aac3b366';
            leaders.beginPath();
            leaders.moveTo(anchor.x, anchor.y);
            leaders.lineTo(Math.max(rect.x, Math.min(rect.x + rect.width, anchor.x)), Math.max(rect.y, Math.min(rect.y + rect.height, anchor.y)));
            leaders.stroke();
        });
    }
    function fitDistance() {
        return Math.max(290, 119 / (Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.min(1, camera.aspect)));
    }
    function resize() {
        needsRender = true;
        bounds = container.getBoundingClientRect();
        camera.aspect = bounds.width / Math.max(1, bounds.height);
        camera.updateProjectionMatrix();
        renderer.setSize(bounds.width, bounds.height);
        composer.setSize(bounds.width, bounds.height);
        leaderCanvas.width = bounds.width;
        leaderCanvas.height = bounds.height;
        const tools = document.getElementById('map-tools').getBoundingClientRect();
        labelObstacles = [{ x: tools.left - bounds.left, y: tools.top - bounds.top, width: tools.width, height: tools.height }];
        camera.position.setLength(THREE.MathUtils.clamp(fitDistance() * zoomRatio, controls.minDistance, controls.maxDistance));
        target = null;
    }
    new ResizeObserver(resize).observe(container);
    function fitNetwork() {
        globe.rotation.set(0, 0, 0);
        zoomRatio = 1;
        flyTo(home, fitDistance());
        closeSite();
    }
    function zoom(factor) {
        const distance = THREE.MathUtils.clamp((target || camera.position).length() * factor, controls.minDistance, controls.maxDistance);
        zoomRatio = distance / fitDistance();
        flyTo(target || camera.position, distance);
    }
    function updatePrefs() {
        const current = prefs();
        earthMaterial.uniforms.tactical.value = current.mapSurface === 'tactical' ? 1 : 0;
        earthMaterial.uniforms.grid.value = current.mapGrid === false ? 0 : 1;
        const fallout = current.uiTheme === 'fallout';
        earthMaterial.uniforms.accent.value.set(current.amberMode ? '#ffd073' : fallout ? '#50ff48' : '#a7cb8b');
        earthMaterial.uniforms.signalGain.value = fallout ? 1.7 : 1;
        bloom.strength = fallout ? 0.3 : 0.18;
        links.visible = current.mapLinks !== false;
        useBloom = !current.lowPower;
        renderer.setPixelRatio(Math.min(devicePixelRatio || 1, current.lowPower ? 1 : 2));
        composer.setPixelRatio(renderer.getPixelRatio());
        space.configure(current, renderer.getPixelRatio());
        document.querySelectorAll('[data-map-surface]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mapSurface === (current.mapSurface || 'relief'))));
        for (const [action, key] of [['grid', 'mapGrid'], ['labels', 'mapLabels'], ['links', 'mapLinks']]) {
            document.querySelector('[data-map-action="' + action + '"]').setAttribute('aria-pressed', String(current[key] !== false));
        }
        renderer.domElement.setAttribute('aria-label', tr('mapCanvas'));
        if (errorKey) showMapMessage(errorKey);
        refreshLabels();
    }
    document.querySelectorAll('[data-map-surface]').forEach(button => button.addEventListener('click', () => {
        window.saveOverseerPrefs({ ...prefs(), mapSurface: button.dataset.mapSurface });
    }));
    document.querySelectorAll('[data-map-action]').forEach(button => button.addEventListener('click', () => {
        viewInteracted = true;
        const action = button.dataset.mapAction;
        if (action === 'zoom-in') zoom(0.82);
        else if (action === 'zoom-out') zoom(1.22);
        else if (action === 'fit') fitNetwork();
        else if (action === 'rotate') {
            setRotation(!rotating);
            window.saveOverseerPrefs({ ...prefs(), mapRotate: rotating });
        } else if (action === 'expand') {
            const expanded = document.body.classList.toggle('map-expanded');
            button.setAttribute('aria-pressed', String(expanded));
            button.dataset.i18nTitle = button.dataset.i18nAria = expanded ? 'mapRestore' : 'mapExpand';
            window.applyOverseerLanguage();
        } else {
            const key = { grid: 'mapGrid', labels: 'mapLabels', links: 'mapLinks' }[action];
            if (key) window.saveOverseerPrefs({ ...prefs(), [key]: prefs()[key] === false });
        }
    }));
    controls.addEventListener('start', () => { viewInteracted = true; target = null; setRotation(false); closeSite(); });
    controls.addEventListener('end', () => { zoomRatio = camera.position.length() / fitDistance(); });
    renderer.domElement.addEventListener('keydown', event => {
        const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', 'Home'];
        if (!keys.includes(event.key)) return;
        viewInteracted = true;
        event.preventDefault();
        if (event.key === 'Home') fitNetwork();
        else if (['+', '=', '-'].includes(event.key)) zoom(event.key === '-' ? 1.22 : 0.82);
        else {
            const spherical = new THREE.Spherical().setFromVector3(camera.position);
            spherical.theta += event.key === 'ArrowLeft' ? 0.12 : event.key === 'ArrowRight' ? -0.12 : 0;
            spherical.phi = THREE.MathUtils.clamp(spherical.phi + (event.key === 'ArrowUp' ? -0.12 : event.key === 'ArrowDown' ? 0.12 : 0), 0.08, Math.PI - 0.08);
            camera.position.setFromSpherical(spherical);
            target = null; setRotation(false);
        }
    });
    const raycaster = new THREE.Raycaster();
    let pointerStart = null;
    renderer.domElement.addEventListener('pointerdown', event => { pointerStart = [event.clientX, event.clientY]; });
    renderer.domElement.addEventListener('click', event => {
        if (!pointerStart || Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) > 5) return;
        raycaster.setFromCamera(new THREE.Vector2((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1), camera);
        const hit = raycaster.intersectObjects([earth, ...sites.map(site => site.marker)], false)[0];
        if (hit?.object.userData.site) openLocation(hit.object.userData.site);
        else closeSite();
    });

    window.resetGlobeZoom = fitNetwork;
    window.focusOverseerNode = id => selectNode(id);
    window.applyOverseerStatuses = refreshLabels;
    window.updateOverseerMapLabels = refreshLabels;
    window.updateOverseerRendererPrefs = updatePrefs;
    window.addEventListener('overseer:prefs', updatePrefs);
    window.addEventListener('overseer:geo', event => {
        if (event.detail.moved) {
            closeSite();
            sites.forEach(site => {
                globe.remove(site.marker);
                site.marker.geometry.dispose(); site.marker.material.dispose();
                site.ring.geometry.dispose(); site.ring.material.dispose();
                site.label.remove();
            });
            sites = OverseerMapGeometry.groupSites(servers);
            createSiteMarkers(); rebuildLinks(); updateHome();
            if (!viewInteracted && sites.length) fitNetwork();
        }
        refreshLabels();
    });
    window.addEventListener('overseer:selection', () => {
        selectedId = window.currentServer?.()?.id || selectedId;
        refreshLabels();
    });
    updatePrefs();
    resize();
    const timer = new THREE.Clock();
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    function animate() {
        requestAnimationFrame(animate);
        const delta = Math.min(timer.getDelta(), 0.05);
        if (document.hidden) return;
        if (rotating && !target && !prefs().lowPower && !reducedMotion.matches) {
            globe.rotation.y += delta * 0.035;
            needsRender = true;
        }
        controls.update();
        if (target) {
            needsRender = true;
            const step = reducedMotion.matches ? 1 : 1 - Math.exp(-delta * 7);
            const distance = THREE.MathUtils.lerp(camera.position.length(), target.length(), step);
            const rotation = new THREE.Quaternion().setFromUnitVectors(camera.position.clone().normalize(), target.clone().normalize());
            camera.position.applyQuaternion(new THREE.Quaternion().slerp(rotation, step)).setLength(distance);
            camera.lookAt(0, 0, 0);
            if (camera.position.distanceTo(target) < 0.1) { camera.position.copy(target); target = null; }
        }
        if (!needsRender) return;
        needsRender = false;
        space.points.position.copy(camera.position);
        const scale = THREE.MathUtils.clamp(camera.position.length() / 340, 0.6, 1.8);
        sites.forEach(site => site.marker.scale.setScalar(scale));
        globe.updateMatrixWorld(true);
        camera.updateMatrixWorld(true);
        updateLabels();
        {
            const local = globe.worldToLocal(camera.position.clone()).normalize();
            const latitude = THREE.MathUtils.radToDeg(Math.asin(local.y));
            const longitude = THREE.MathUtils.radToDeg(Math.atan2(-local.z, local.x));
            document.getElementById('map-view-coordinates').textContent = Math.abs(latitude).toFixed(1) + '° ' + (latitude >= 0 ? 'N' : 'S') + ' / ' + Math.abs(longitude).toFixed(1) + '° ' + (longitude >= 0 ? 'E' : 'W');
        }
        if (useBloom) composer.render(); else renderer.render(scene, camera);
    }
    animate();
})();
