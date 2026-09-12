"""Synthesize original, quiet flight foley. Requires NumPy and FFmpeg (Vorbis)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1] / 'pack/assets/oak_aviary'
RATE = 24000


def sound(name, seconds, seed, cutoff, gain):
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * RATE)) / RATE
    noise = rng.normal(size=len(t))
    spectrum = np.fft.rfft(noise)
    frequencies = np.fft.rfftfreq(len(t), 1 / RATE)
    # Broad, soft feather noise without sharp hiss or a repeating pitched motor.
    spectrum *= (1 - np.exp(-(frequencies / 110) ** 2)) / (1 + (frequencies / cutoff) ** 4)
    signal = np.fft.irfft(spectrum, len(t))
    envelope = np.sin(np.pi * t / seconds) ** 2
    if name == 'land':
        envelope *= np.exp(-t * 8)
        signal += .15 * np.sin(2 * np.pi * 105 * t) * np.exp(-t * 18)
    elif name == 'saddle':
        envelope *= .6 + .4 * np.cos(t * 48)
    signal *= envelope
    signal *= gain / max(.001, np.max(np.abs(signal)))
    with tempfile.TemporaryDirectory() as directory:
        wav = Path(directory) / 'source.wav'
        with wave.open(str(wav), 'wb') as stream:
            stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            stream.writeframes((signal * 32767).astype('<i2').tobytes())
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-c:a', 'libvorbis', '-q:a', '4', str(ROOT / 'sounds' / (name + '.ogg'))], check=True)


def call(name):
    """Original short, breathy calls with continuous pitch and soft endpoints."""
    seconds = .48 if name == 'whistle' else .70
    t = np.arange(round(seconds * RATE)) / RATE
    rng = np.random.default_rng(105 if name == 'whistle' else 106)
    if name == 'whistle':
        frequency = 1040 + 155 * np.sin(np.pi * t / seconds) ** 2
        envelope = np.sin(np.pi * t / seconds) ** 1.5
        harmonics = (1, .14, .045)
    else:
        frequency = 360 + 105 * np.exp(-t * 6) + 7 * np.sin(t * 29)
        envelope = np.sin(np.pi * t / seconds) ** 1.4 * (1 - .24 * np.exp(-((t-.30)/.045)**2))
        harmonics = (1, .26, .09)
    phase = np.cumsum(frequency) * (2 * np.pi / RATE)
    signal = sum(gain*np.sin(phase*(n+1)) for n, gain in enumerate(harmonics))
    noise = rng.normal(size=len(t))
    spectrum = np.fft.rfft(noise)
    frequencies = np.fft.rfftfreq(len(t), 1/RATE)
    spectrum *= (1 - np.exp(-(frequencies/350)**2)) / (1+(frequencies/1800)**4)
    signal = (signal + .10*np.fft.irfft(spectrum, len(t))) * envelope
    signal *= .32/max(.001, np.max(np.abs(signal)))
    with tempfile.TemporaryDirectory() as directory:
        wav = Path(directory)/'call.wav'
        with wave.open(str(wav), 'wb') as stream:
            stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            stream.writeframes((signal*32767).astype('<i2').tobytes())
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-c:a', 'libvorbis', '-q:a', '4', str(ROOT/'sounds'/f'{name}.ogg')], check=True)


if __name__ == '__main__':
    (ROOT / 'sounds').mkdir(parents=True, exist_ok=True)
    if '--calls-only' not in sys.argv:
        for spec in [('wing_power', .62, 81, 1400, .55), ('wing_glide', .43, 82, 1850, .36), ('saddle', .28, 83, 750, .30), ('land', .36, 84, 950, .40)]:
            sound(*spec)
    for name in ('whistle', 'reply'):
        call(name)
    metadata = json.loads((ROOT/'sounds.json').read_text(encoding='utf-8')) if (ROOT/'sounds.json').exists() else {}
    metadata.update({name: {'sounds': [{'name': 'oak_aviary:' + name, 'attenuation_distance': 12}]} for name in ('wing_power', 'wing_glide', 'saddle', 'land', 'whistle', 'reply')})
    (ROOT / 'sounds.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
