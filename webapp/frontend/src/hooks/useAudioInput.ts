import { useCallback, useEffect, useRef, useState } from 'react';

const MAX_DURATION_SEC = 60;

type Status = 'idle' | 'recording' | 'transcribing' | 'error';

export function useAudioInput(onTranscribed?: (text: string) => void) {
  const [status, setStatus] = useState<Status>('idle');
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState<boolean>(true);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number>(0);

  useEffect(() => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia ||
        typeof window.MediaRecorder === 'undefined') {
      setSupported(false);
    }
  }, []);

  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    recorderRef.current = null;
    chunksRef.current = [];
  }, []);

  const stop = useCallback(() => {
    const rec = recorderRef.current;
    if (rec && rec.state !== 'inactive') {
      rec.stop();
    }
  }, []);

  const start = useCallback(async () => {
    if (!supported) {
      setError('Microfono non disponibile nel browser');
      setStatus('error');
      return;
    }
    setError(null);
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorderRef.current = rec;

      rec.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };

      rec.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' });
        cleanup();
        if (blob.size === 0) {
          setStatus('idle');
          setElapsed(0);
          return;
        }
        setStatus('transcribing');
        try {
          const fd = new FormData();
          const ext = (rec.mimeType || 'audio/webm').includes('ogg') ? 'ogg' : 'webm';
          fd.append('audio', blob, `chat_${Date.now()}.${ext}`);
          const res = await fetch('http://localhost:8000/api/transcribe', {
            method: 'POST',
            body: fd,
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({} as any));
            throw new Error(err?.detail || `HTTP ${res.status}`);
          }
          const data = await res.json();
          const text = (data.text || '').trim();
          setElapsed(0);
          if (text && onTranscribed) {
            setStatus('idle');
            onTranscribed(text);
          } else {
            // Trascrizione vuota: dai feedback all'utente invece di restare silenzioso
            setError('Nessun testo rilevato. Parla piu\' vicino al microfono o riprova.');
            setStatus('error');
          }
        } catch (e: any) {
          setError(e?.message || 'Trascrizione fallita');
          setStatus('error');
        }
      };

      startedAtRef.current = Date.now();
      setElapsed(0);
      timerRef.current = window.setInterval(() => {
        const s = Math.floor((Date.now() - startedAtRef.current) / 1000);
        setElapsed(s);
        if (s >= MAX_DURATION_SEC) stop();
      }, 250);

      rec.start();
      setStatus('recording');
    } catch (e: any) {
      cleanup();
      setError(e?.message || 'Accesso al microfono negato');
      setStatus('error');
    }
  }, [cleanup, onTranscribed, stop, supported]);

  useEffect(() => () => cleanup(), [cleanup]);

  const reset = useCallback(() => {
    setError(null);
    setStatus('idle');
    setElapsed(0);
  }, []);

  return {
    status,
    isRecording: status === 'recording',
    isTranscribing: status === 'transcribing',
    elapsed,
    error,
    supported,
    start,
    stop,
    reset,
  };
}
