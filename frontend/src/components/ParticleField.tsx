import { useEffect, useRef } from "react";

interface Particle {
  x: number;
  y: number;
  z: number;
  size: number;
  tone: number;
  drift: number;
}

export function ParticleField() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }
    const activeCanvas = canvas;
    const activeContext = context;

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const pointer = { x: 0, y: 0 };
    let width = 0;
    let height = 0;
    let frame = 0;
    let animationFrame = 0;
    let particles: Particle[] = [];

    function createParticles() {
      const count = width < 720 ? 34 : Math.min(82, Math.floor(width / 17));
      particles = Array.from({ length: count }, (_, index) => ({
        x: (Math.random() - 0.5) * width * 1.15,
        y: (Math.random() - 0.5) * height * 0.9,
        z: Math.random() * 780 - 220,
        size: 0.8 + Math.random() * 2.4,
        tone: index % 9 === 0 ? 1 : 0,
        drift: 0.16 + Math.random() * 0.34,
      }));
    }

    function resize() {
      const bounds = activeCanvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = bounds.width;
      height = bounds.height;
      activeCanvas.width = Math.max(1, Math.floor(width * ratio));
      activeCanvas.height = Math.max(1, Math.floor(height * ratio));
      activeContext.setTransform(ratio, 0, 0, ratio, 0, 0);
      createParticles();
    }

    function draw() {
      activeContext.clearRect(0, 0, width, height);
      const centerX = width * 0.62 + pointer.x * 28;
      const centerY = height * 0.46 + pointer.y * 18;
      const projected: Array<{ x: number; y: number; scale: number; particle: Particle }> = [];

      particles.forEach((particle) => {
        if (!reducedMotion.matches) {
          particle.z -= particle.drift;
          particle.x += Math.sin((frame + particle.z) * 0.003) * 0.06;
          particle.y += Math.cos((frame + particle.x) * 0.002) * 0.045;
        }
        if (particle.z < -260) {
          particle.z = 560;
          particle.x = (Math.random() - 0.5) * width * 1.1;
          particle.y = (Math.random() - 0.5) * height * 0.9;
        }

        const perspective = 560 / (560 + particle.z);
        projected.push({
          x: centerX + particle.x * perspective,
          y: centerY + particle.y * perspective,
          scale: Math.max(0.16, perspective),
          particle,
        });
      });

      projected.forEach((point, index) => {
        projected.slice(index + 1, index + 8).forEach((neighbor) => {
          const distance = Math.hypot(point.x - neighbor.x, point.y - neighbor.y);
          if (distance < 92) {
            activeContext.beginPath();
            activeContext.moveTo(point.x, point.y);
            activeContext.lineTo(neighbor.x, neighbor.y);
            activeContext.strokeStyle = `rgba(244, 238, 224, ${0.075 * (1 - distance / 92)})`;
            activeContext.lineWidth = 0.6;
            activeContext.stroke();
          }
        });

        const radius = point.particle.size * point.scale;
        const glow = activeContext.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius * 5);
        glow.addColorStop(0, point.particle.tone ? "rgba(232, 86, 57, 0.92)" : "rgba(249, 243, 229, 0.82)");
        glow.addColorStop(0.22, point.particle.tone ? "rgba(232, 86, 57, 0.32)" : "rgba(249, 243, 229, 0.22)");
        glow.addColorStop(1, "rgba(0, 0, 0, 0)");
        activeContext.fillStyle = glow;
        activeContext.beginPath();
        activeContext.arc(point.x, point.y, Math.max(2, radius * 5), 0, Math.PI * 2);
        activeContext.fill();

        activeContext.fillStyle = point.particle.tone ? "#e85639" : "#f4eee0";
        activeContext.beginPath();
        activeContext.arc(point.x, point.y, Math.max(0.55, radius), 0, Math.PI * 2);
        activeContext.fill();
      });

      frame += 1;
      if (!reducedMotion.matches) {
        animationFrame = window.requestAnimationFrame(draw);
      }
    }

    function handlePointerMove(event: PointerEvent) {
      pointer.x = event.clientX / Math.max(window.innerWidth, 1) - 0.5;
      pointer.y = event.clientY / Math.max(window.innerHeight, 1) - 0.5;
    }

    resize();
    draw();
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", handlePointerMove, { passive: true });

    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", handlePointerMove);
    };
  }, []);

  return <canvas className="particle-field" ref={canvasRef} aria-hidden />;
}
