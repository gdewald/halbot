import { useState, useEffect } from 'react';
import { T } from '../tokens.js';

export function StatusBar({ panel }) {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div style={{
      height: 22, flexShrink: 0, background: T.blurple,
      display: 'flex', alignItems: 'center', padding: '0 10px', gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <div style={{
          width: 6, height: 6, borderRadius: '50%',
          background: 'rgba(255,255,255,0.7)',
          animation: 'pulse 2s ease-in-out infinite',
        }} />
        <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.85)', fontFamily: 'JetBrains Mono' }}>
          connected
        </span>
      </div>
      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.5)' }}>|</span>
      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.75)', fontFamily: 'JetBrains Mono' }}>
        halbot
      </span>
      <div style={{ flex: 1 }} />
      <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.65)', fontFamily: 'JetBrains Mono' }}>
        {time.toTimeString().slice(0, 8)}
      </span>
    </div>
  );
}
