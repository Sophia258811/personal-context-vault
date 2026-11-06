let capture;
const MAX_SESSION_MS = 10 * 60 * 1000;
const STOP_WITHOUT_PLAYBACK_MS = 15000;
const CLIP_MS = 6000;

function stopCapture(session, notify) {
  if (session !== capture) return;
  capture = undefined;
  clearInterval(session.watchdog);
  session.segment = null;
  session.stream?.getTracks().forEach(track => track.stop());
  if (session.video) {
    session.video.pause();
    session.video.srcObject = null;
    session.video.remove();
  }
  if (session.context) session.context.close().catch(() => {});
  if (notify) {
    chrome.runtime.sendMessage({target: 'pcv-media', action: 'ended', session: session.id}).catch(() => {});
  }
}

async function waitForStartup(operation, timeout) {
  let timer;
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new DOMException('Startup timed out', 'TimeoutError')), timeout);
    })]);
  } finally {
    clearTimeout(timer);
  }
}

function captureFailure(stage, error) {
  const reason = ({
    TimeoutError: 'timeout',
    NotAllowedError: 'permission',
    NotFoundError: 'missing',
    NotReadableError: 'unreadable',
    AbortError: 'aborted'
  })[error?.name] || 'failed';
  return {ok: false, diagnostic: {stage, reason}};
}

async function startCapture(message) {
  if (capture) return {ok: false};
  const session = {id: message.session, lastPlaying: Date.now(), started: Date.now()};
  capture = session;
  let stage = 'get_user_media';
  try {
    const constraints = {mandatory: {chromeMediaSource: 'tab', chromeMediaSourceId: message.streamId}};
    const stream = await waitForStartup(
      navigator.mediaDevices.getUserMedia({audio: constraints, video: constraints}).then(result => {
        if (session === capture) session.stream = result;
        else result.getTracks().forEach(track => track.stop());
        return result;
      }), 4500
    );
    if (session !== capture) return {ok: false};
    for (const track of stream.getTracks()) {
      track.addEventListener('ended', () => stopCapture(session, true));
    }
    stage = 'audio_resume';
    const context = new AudioContext();
    session.context = context;
    const source = context.createMediaStreamSource(stream);
    source.connect(context.destination);
    if (context.state !== 'running') await waitForStartup(context.resume(), 2500);
    if (session !== capture) return {ok: false};
    if (context.state !== 'running') throw new DOMException('Audio unavailable', 'NotAllowedError');
    stage = 'audio_worklet';
    await waitForStartup(context.audioWorklet.addModule('media-audio.js'), 3000);
    if (session !== capture) return {ok: false};
    const tap = new AudioWorkletNode(context, 'pcv-audio-tap');
    tap.port.onmessage = ({data}) => {
      if (session.segment && session.beat?.audible && data.startFrame >= session.segment.audioStartFrame &&
          Date.now() - session.beat.at <= pcvMedia.IDLE_MS) session.segment.audio.push(data.samples);
    };
    source.connect(tap);
    // The tap outputs silence. This connection keeps its processor scheduled.
    tap.connect(context.destination);
    stage = 'video_play';
    const video = document.createElement('video');
    session.video = video;
    video.muted = true;
    video.srcObject = new MediaStream(stream.getVideoTracks());
    document.body.append(video);
    await waitForStartup(video.play(), 3000);
    if (session !== capture) return {ok: false};
    session.lastPlaying = Date.now();
    session.watchdog = setInterval(() => {
      const now = Date.now();
      if (now - session.started > MAX_SESSION_MS || now - session.lastPlaying > STOP_WITHOUT_PLAYBACK_MS) {
        stopCapture(session, true);
      } else if (session.beat && now - session.beat.at > pcvMedia.IDLE_MS) {
        session.beat = null;
        session.segment = null;
      }
    }, 250);
    return {ok: true};
  } catch (error) {
    stopCapture(session, false);
    return captureFailure(stage, error);
  }
}

function keyframe(video, beat) {
  if (video?.readyState < 2 || !video?.videoWidth || !video.videoHeight) return null;
  const rect = pcvMedia.crop(beat, video.videoWidth, video.videoHeight);
  if (!rect) return null;
  const scale = Math.min(1, 640 / Math.max(rect.w, rect.h));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(rect.w * scale);
  canvas.height = Math.round(rect.h * scale);
  canvas.getContext('2d').drawImage(video, rect.x, rect.y, rect.w, rect.h, 0, 0, canvas.width, canvas.height);
  const url = canvas.toDataURL('image/jpeg', 0.7);
  return url.startsWith('data:image/jpeg;base64,') && url.length < 300000
    ? {position_seconds: beat.position_seconds, jpeg: url.split(',')[1]} : null;
}

function base64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 16384) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  }
  return btoa(binary);
}

function updateBeat(session, beat) {
  const previous = session.beat;
  session.beat = beat;
  if (!beat || Date.now() - beat.at > pcvMedia.IDLE_MS) {
    session.segment = null;
    return;
  }
  session.lastPlaying = beat.at;
  if (!session.segment || !pcvMedia.continues(previous, beat)) {
    session.segment = {
      first: beat, frame: keyframe(session.video, beat), audio: [],
      audioStartFrame: Math.ceil(session.context.currentTime * session.context.sampleRate)
    };
    return;
  }
  const part = session.segment;
  const span = beat.position_seconds - part.first.position_seconds;
  if (beat.at - part.first.at < CLIP_MS && span < 10) return;
  session.segment = null;
  if (!(span > 0 && span <= 15) || session.sending) return;
  const audio = pcvMedia.wav16(part.audio, session.context.sampleRate, span / beat.rate);
  const frame = part.frame || keyframe(session.video, beat);
  if (!audio && !frame) return;
  session.sending = true;
  chrome.runtime.sendMessage({
    target: 'pcv-media', action: 'upload', session: session.id,
    segment: {
      id: crypto.randomUUID(), platform_content_id: beat.platform_content_id,
      start_seconds: part.first.position_seconds, end_seconds: beat.position_seconds,
      audio: audio ? base64(audio) : null, frames: frame ? [frame] : []
    }
  }).catch(() => {}).finally(() => { session.sending = false; });
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message.target !== 'pcv-offscreen' || sender.tab) return false;
  if (message.action === 'start') {
    startCapture(message).then(reply, () => reply({ok: false}));
    return true;
  }
  const session = capture;
  if (session?.id === message.session) {
    if (message.action === 'beat') updateBeat(session, message.beat);
    if (message.action === 'stop') stopCapture(session, false);
  }
  reply({ok: true});
  return false;
});
