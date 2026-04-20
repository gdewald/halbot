import { useEffect, useRef, useState, useCallback } from 'react';
import { b } from '../../bridge.js';

const MAX = 2000;
const POLL_MS = 250;

export function useLogStream() {
  const [logs, setLogs] = useState([]);
  const [connected, setConnected] = useState(false);
  const mounted = useRef(true);

  const push = useCallback((batch) => {
    if (!batch || batch.length === 0) return;
    setLogs(prev => {
      const next = prev.concat(batch);
      return next.length > MAX ? next.slice(next.length - MAX) : next;
    });
  }, []);

  useEffect(() => {
    mounted.current = true;
    let timer = null;

    (async () => {
      try {
        const backlog = await b.backlogLogs(400);
        if (!mounted.current) return;
        push(backlog);
        setConnected(backlog.length > 0);
      } catch (e) {
        setConnected(false);
      }
    })();

    const tick = async () => {
      if (!mounted.current) return;
      try {
        const batch = await b.popLogBatch(200);
        if (!mounted.current) return;
        if (batch.length) {
          push(batch);
          setConnected(true);
        }
      } catch (e) {
        setConnected(false);
      } finally {
        if (mounted.current) timer = setTimeout(tick, POLL_MS);
      }
    };
    timer = setTimeout(tick, POLL_MS);

    return () => {
      mounted.current = false;
      if (timer) clearTimeout(timer);
    };
  }, [push]);

  const clear = useCallback(() => setLogs([]), []);

  return { logs, connected, clear };
}
