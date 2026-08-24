import { useEffect, useRef } from "react";

const VERTEX_SHADER = `
  attribute vec2 position;
  void main() {
    gl_Position = vec4(position, 0.0, 1.0);
  }
`;

const FRAGMENT_SHADER = `
  precision mediump float;
  uniform vec2 resolution;
  uniform float time;

  float glow(vec2 uv, vec2 center, float size) {
    float d = length(uv - center);
    return smoothstep(size, 0.0, d);
  }

  void main() {
    vec2 uv = gl_FragCoord.xy / resolution.xy;
    uv.x *= resolution.x / resolution.y;
    float aspect = resolution.x / resolution.y;

    vec2 a = vec2(0.16 * aspect + sin(time * .10) * .07, .84 + cos(time * .13) * .05);
    vec2 b = vec2(0.73 * aspect + cos(time * .08) * .10, .18 + sin(time * .11) * .06);
    vec2 c = vec2(0.92 * aspect + sin(time * .07) * .06, .70 + cos(time * .09) * .08);

    vec3 base = vec3(.935, .94, .985);
    vec3 violet = vec3(.26, .13, .63) * glow(uv, a, .68);
    vec3 mint = vec3(.02, .72, .48) * glow(uv, b, .52);
    vec3 blue = vec3(.09, .35, .76) * glow(uv, c, .70);
    float wave = sin((uv.x + uv.y) * 8.0 + time * .22) * .012;
    vec3 color = base + violet * .23 + mint * .12 + blue * .15 + wave;
    gl_FragColor = vec4(color, 1.0);
  }
`;

function createShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

export function WebGLGlass() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl", {
      alpha: false,
      antialias: false,
      powerPreference: "low-power"
    });
    if (!gl) return;

    const vertex = createShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
    const fragment = createShader(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER);
    const program = gl.createProgram();
    if (!vertex || !fragment || !program) return;
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;

    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW
    );
    gl.useProgram(program);
    const position = gl.getAttribLocation(program, "position");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    const resolution = gl.getUniformLocation(program, "resolution");
    const time = gl.getUniformLocation(program, "time");
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frame = 0;

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      const width = Math.floor(canvas.clientWidth * ratio);
      const height = Math.floor(canvas.clientHeight * ratio);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        gl.viewport(0, 0, width, height);
      }
    };

    const draw = (stamp: number) => {
      resize();
      gl.uniform2f(resolution, canvas.width, canvas.height);
      gl.uniform1f(time, stamp * 0.001);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      if (!reduceMotion) frame = window.requestAnimationFrame(draw);
    };

    draw(0);
    window.addEventListener("resize", resize);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
    };
  }, []);

  return <canvas className="webgl-glass" ref={canvasRef} aria-hidden />;
}
