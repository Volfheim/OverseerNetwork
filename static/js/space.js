window.createOverseerSpace = function (THREE) {
    const count = 2600;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    let seed = 2077;
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < count; i++) {
        const theta = random() * Math.PI * 2;
        const band = i % 3 === 0;
        const phi = band ? Math.PI / 2 + (random() - 0.5) * 0.3 : Math.acos(random() * 2 - 1);
        const point = new THREE.Vector3().setFromSphericalCoords(1000, phi, theta);
        point.applyAxisAngle(new THREE.Vector3(0, 0, 1), 0.55);
        positions.set(point.toArray(), i * 3);
        const warm = random() > 0.8;
        const brightness = 0.4 + random() * 0.6;
        colors.set([brightness * (warm ? 1 : 0.8), brightness * 0.94, brightness * (warm ? 0.65 : 1)], i * 3);
        sizes[i] = random() > 0.96 ? 3.8 : 1.4 + random() * 1.4;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute('starSize', new THREE.BufferAttribute(sizes, 1));
    const material = new THREE.ShaderMaterial({
        transparent: true, depthWrite: false, vertexColors: true,
        uniforms: { pixelRatio: { value: 1 }, brightness: { value: 1 } },
        vertexShader: 'attribute float starSize; uniform float pixelRatio; varying vec3 vColor; void main() { vColor = color; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_PointSize = starSize * pixelRatio; }',
        fragmentShader: 'varying vec3 vColor; uniform float brightness; void main() { float d = length(gl_PointCoord - 0.5); float alpha = 1.0 - smoothstep(0.05, 0.5, d); gl_FragColor = vec4(vColor * brightness, alpha * 0.9); }',
    });
    const points = new THREE.Points(geometry, material);
    points.frustumCulled = false;
    return {
        points,
        configure(prefs, pixelRatio) {
            points.visible = prefs.spaceBackground !== false;
            geometry.setDrawRange(0, prefs.lowPower ? 450 : count);
            material.uniforms.pixelRatio.value = pixelRatio;
            material.uniforms.brightness.value = 1.3;
        },
    };
};
