using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    /// <summary>Local presentation-only audio. It never owns assistant speech audio.</summary>
    public sealed class PresentationAudio : MonoBehaviour
    {
        public const string SfxMutedKey = "AIFren.UiSfxMuted";
        public const string SfxVolumeKey = "AIFren.UiSfxVolume";
        public const string BgmMutedKey = "AIFren.BgmMuted";
        public const string BgmVolumeKey = "AIFren.BgmVolume";
        private AudioSource sfx;
        private AudioSource bgm;
        private AudioClip tap;
        private AudioClip interrupt;

        public bool SfxMuted => PlayerPrefs.GetInt(SfxMutedKey, 0) == 1;
        public bool BgmMuted => PlayerPrefs.GetInt(BgmMutedKey, 0) == 1;
        public float SfxVolume => PlayerPrefs.GetFloat(SfxVolumeKey, .45f);
        public float BgmVolume => PlayerPrefs.GetFloat(BgmVolumeKey, .14f);

        public void Initialize()
        {
            sfx = gameObject.AddComponent<AudioSource>();
            sfx.playOnAwake = false;
            bgm = gameObject.AddComponent<AudioSource>();
            bgm.playOnAwake = false;
            bgm.loop = true;
            tap = Resources.Load<AudioClip>("Presentation/Audio/ui_tap");
            interrupt = Resources.Load<AudioClip>("Presentation/Audio/interrupt_cue");
            bgm.clip = Resources.Load<AudioClip>("Presentation/Audio/cozy_vn_piano_loop");
            Apply();
            // Start the loop regardless of its persisted mute state so a
            // later unmute is immediately correct; AudioSource.mute keeps it
            // silent from the very first frame when the saved state is muted.
            if (bgm.clip != null && !bgm.isPlaying) bgm.Play();
        }

        public void PlayTap() { if (!SfxMuted && tap != null) sfx.PlayOneShot(tap, SfxVolume); }
        public void PlayInterrupt() { if (!SfxMuted && interrupt != null) sfx.PlayOneShot(interrupt, SfxVolume); }
        public void SetSfxMuted(bool value) { PlayerPrefs.SetInt(SfxMutedKey, value ? 1 : 0); SaveAndApply(); }
        public void SetBgmMuted(bool value) { PlayerPrefs.SetInt(BgmMutedKey, value ? 1 : 0); SaveAndApply(); }
        public void SetSfxVolume(float value) { PlayerPrefs.SetFloat(SfxVolumeKey, Mathf.Clamp01(value)); SaveAndApply(); }
        public void SetBgmVolume(float value) { PlayerPrefs.SetFloat(BgmVolumeKey, Mathf.Clamp01(value)); SaveAndApply(); }

        /// <summary>Resets only local presentation-audio preferences, never audio files or history.</summary>
        public void ResetToDefaults()
        {
            PlayerPrefs.DeleteKey(SfxMutedKey);
            PlayerPrefs.DeleteKey(SfxVolumeKey);
            PlayerPrefs.DeleteKey(BgmMutedKey);
            PlayerPrefs.DeleteKey(BgmVolumeKey);
            SaveAndApply();
        }
        private void SaveAndApply() { PlayerPrefs.Save(); Apply(); }
        private void Apply()
        {
            if (sfx != null)
            {
                sfx.volume = SfxVolume;
                sfx.mute = SfxMuted;
            }
            if (bgm == null) return;
            bgm.volume = BgmVolume;
            bgm.mute = BgmMuted;
        }
    }
}
