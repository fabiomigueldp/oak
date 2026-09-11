"""Synthesize original, quiet flight foley. Requires NumPy and FFmpeg (Vorbis)."""
import json
from pathlib import Path
import subprocess
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


if __name__ == '__main__':
    (ROOT / 'sounds').mkdir(parents=True, exist_ok=True)
    for spec in [('wing_power', .62, 81, 1400, .55), ('wing_glide', .43, 82, 1850, .36), ('saddle', .28, 83, 750, .30), ('land', .36, 84, 950, .40)]:
        sound(*spec)
    (ROOT / 'sounds.json').write_text(json.dumps({name: {'sounds': [{'name': 'oak_aviary:' + name, 'attenuation_distance': 12}]} for name in ('wing_power', 'wing_glide', 'saddle', 'land')}, indent=2) + '\n', encoding='utf-8')
