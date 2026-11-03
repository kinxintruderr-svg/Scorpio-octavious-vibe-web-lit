
// ads.js - SOV LITE AdMob linker
let adConfig = {};
let playCount = parseInt(localStorage.getItem('sov_playcount') || '0');

fetch('/admob-config')
  .then(r => r.json())
  .then(cfg => {
    adConfig = cfg;
    console.log("AdMob linked", cfg.banner);
    const bannerDiv = document.getElementById('banner-ad');
    if(bannerDiv) bannerDiv.innerHTML = "AD: " + cfg.banner; // replace with real ad later
  });

function triggerAdOnPlay(){
  playCount++;
  localStorage.setItem('sov_playcount', playCount);
  if(playCount % 3 === 0 && adConfig.interstitial){
    if(window.Android && window.Android.showInterstitial){
      window.Android.showInterstitial(adConfig.interstitial);
    } else {
      console.log("Show Interstitial:", adConfig.interstitial);
    }
  }
}

// auto attach to all songs - add class="song" to your song items
document.addEventListener('click', (e)=>{
  if(e.target.closest('.song')){
    triggerAdOnPlay();
  }
});


