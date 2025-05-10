// Keep-Alive fonksiyonu - uygulamayı aktif tutmak için
function keepAlive() {
    // Her 4 dakikada bir kendi sunucumuza istek at
    setInterval(() => {
        fetch(window.location.href + '?keepalive=' + new Date().getTime())
            .then(response => console.log('Keep-alive sinyal gönderildi'))
            .catch(err => console.log('Keep-alive hatası:', err));
    }, 240000); // 4 dakika = 240000 milisaniye
}

// DOM elementlerini seç
const exchangeSelect = document.getElementById('exchange-select');
const timeframeSelect = document.getElementById('timeframe-select');
const lookbackInput = document.getElementById('lookback-input');
const startScanBtn = document.getElementById('start-scan-btn');
const stopScanBtn = document.getElementById('stop-scan-btn');
const signalTable = document.getElementById('signal-table').querySelector('tbody');
const statusMessage = document.getElementById('status-message');
const progressBar = document.getElementById('progress-bar');
const signalCount = document.getElementById('signal-count');
const lastScanTime = document.getElementById('last-scan-time');
const darkModeToggle = document.getElementById('dark-mode-toggle');

// Global değişkenler
let isScanning = false;
let signals = [];
let socket = null;

// Sayfa yüklendiğinde
document.addEventListener('DOMContentLoaded', () => {
    // WebSocket bağlantısı oluştur
    connectWebSocket();
    
    // Mevcut sinyalleri yükle
    loadSignals();
    
    // Event dinleyicilerini ekle
    setupEventListeners();
    
    // Karanlık/aydınlık mod ayarlarını yükle
    loadThemePreference();
    
    // Güncel zaman göster
    updateCurrentTime();
    
    // Keep-Alive fonksiyonunu başlat - uygulamayı aktif tutmak için
    keepAlive();
});

// WebSocket bağlantısı oluştur
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    
    // Socket.io bağlantısı kur
    socket = io();
    
    // Socket olaylarını dinle
    socket.on('connect', () => {
        console.log('Socket.io bağlantısı kuruldu');
    });
    
    socket.on('disconnect', () => {
        console.log('Socket.io bağlantısı kesildi');
    });
    
    // Durum ve ilerleme güncellemeleri
    socket.on('status_update', (data) => {
        updateStatus(data.message);
    });
    
    socket.on('progress_update', (data) => {
        updateProgress(data.progress);
    });
    
    // Yeni Socket.IO event formatları
    socket.on('scan_status', (data) => {
        console.log('Tarama durumu:', data);
        updateStatus(data.message || data.type);
        if (data.type === 'complete') {
            setScanning(false);
            updateLastScanTime();
        }
    });
    
    // YENI - TOPLU SİNYAL İŞLEME - Çok daha hızlı tablo güncellemesi
    socket.on('batch_signals', (batchData) => {
        console.log('Toplu sinyaller:', batchData);
        if (Array.isArray(batchData)) {
            // Her sinyal için hızlı döngü
            batchData.forEach(signal => {
                addSignal(signal); // Her biri için sinyal ekle fonksiyonu çağır
            });
            // Tüm sinyaller tamamlandığında UI güncelleme
            updateSignalCount();
        }
    });
    
    // BİRDEN FAZLA OLAY DİNLE - Geriye uyumluluk için
    // Yeni Sinyal olayları
    socket.on('new_signal', (data) => {
        console.log('Yeni sinyal:', data);
        addSignal(data);
    });
    
    socket.on('scan_result', (data) => {
        console.log('Tarama sonucu:', data);
        if (data.symbol && data.timeframe) {
            addSignal(data);
        }
    });
    
    // Tüm sonuçları al
    socket.on('all_results', (data) => {
        console.log('Tüm sonuçlar:', data);
        if (data.results && Array.isArray(data.results)) {
            // Tüm sonuçları ekle
            data.results.forEach(signal => {
                addSignal(signal);
            });
        }
    });
    
    // Tarama tamamlandı olayı
    socket.on('scan_complete', (data) => {
        console.log('Tarama tamamlandı', data);
        setScanning(false);
        
        // Count bilgisi farklı formatlarda gelebilir
        if (data.count !== undefined) {
            updateSignalCount(data.count);
        } else if (data.message && data.message.includes('sonuç')) {
            // Mesajdan sayıyı çıkar
            const match = data.message.match(/(\d+)\s+sonuç/);
            if (match && match[1]) {
                updateSignalCount(match[1]);
            } else {
                updateSignalCount(signals.length);
            }
        } else {
            // Hiçbir bilgi yoksa mevcut sinyal sayısını göster
            updateSignalCount(signals.length);
        }
        
        updateLastScanTime();
    });
}

// Tarama başlatma
function startScan() {
    // Tarama butonlarını düzenle
    setScanning(true);
    
    // Ayarları al
    const settings = getFilterSettings();
    
    // Önce ayarları güncelle
    fetch('/update_settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // SOCKET.IO KULLANARAK TARAMAYI BAŞSLAT (HTTP yerine)
            console.log('Socket.io ile tarama başlatılıyor...');
            
            // Taramayı Socket.io ile başlat
            socket.emit('start_scan', {
                timeframe: settings.selected_timeframe,
                symbols_to_scan: 'all'
            }, (response) => {
                // Opsiyonel callback - sunucu yanıt verirse
                if (response && response.status === 'success') {
                    updateStatus(response.message);
                }
            });
            
            // Tarama durumunu güncelle
            updateStatus('Tarama başlatılıyor...');
        } else {
            setScanning(false); // Hata durumunda durdur butonunu gizle
            updateStatus(`Hata: ${data.message}`);
        }
    })
    .catch(error => {
        setScanning(false); // Hata durumunda durdur butonunu gizle
        updateStatus(`Hata: ${error.message}`);
    });
}

// Tarama durdurma
function stopScan() {
    fetch('/stop_scan', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            setScanning(false);
            updateStatus(data.message);
        } else {
            updateStatus(`Hata: ${data.message}`);
        }
    })
    .catch(error => {
        updateStatus(`Hata: ${error.message}`);
    });
}

// Özel coin tarama
function scanSingleCoin() {
    const coinSymbolInput = document.getElementById('coin-symbol');
    const symbol = coinSymbolInput.value.trim().toUpperCase();
    
    if (!symbol) {
        updateStatus('Lütfen bir coin sembolü girin');
        return;
    }
    
    const settings = getFilterSettings();
    settings.symbol = symbol;
    
    updateStatus(`${symbol} coin'i taraması başlatılıyor...`);
    updateProgress(10);
    
    fetch('/scan_coin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            updateStatus(data.message);
            updateProgress(100);
        } else {
            updateStatus(`Hata: ${data.message}`);
            updateProgress(0);
        }
    })
    .catch(error => {
        updateStatus(`Hata: ${error.message}`);
        updateProgress(0);
    });
}

// Tabloyu temizleme fonksiyonu
function clearSignalTable() {
    // Sinyalleri temizle
    signals = [];
    // Tabloyu güncelle
    updateSignalTable();
    // Sinyal sayısını sıfırla
    updateSignalCount(0);
    // Bildirim göster
    updateStatus('Sinyal tablosu temizlendi');
    
    // Sunucudan da sinyalleri temizle
    fetch('/clear_signals', {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        console.log('Sunucu sinyalleri temizlendi:', data);
    })
    .catch(error => {
        console.error('Sunucu sinyalleri temizlenemedi:', error);
    });
}

// Event dinleyicileri
function setupEventListeners() {
    // Tarama başlatma/durdurma
    startScanBtn.addEventListener('click', startScan);
    stopScanBtn.addEventListener('click', stopScan);
    
    // Özel coin tarama
    const scanCoinBtn = document.getElementById('scan-coin-btn');
    scanCoinBtn.addEventListener('click', scanSingleCoin);
    
    // Tabloyu temizleme butonu
    const clearTableBtn = document.getElementById('clear-table-btn');
    if (clearTableBtn) {
        clearTableBtn.addEventListener('click', clearSignalTable);
    }
    
    // Coin input'u için Enter tuşu dinleyicisi
    const coinSymbolInput = document.getElementById('coin-symbol');
    coinSymbolInput.addEventListener('keyup', (event) => {
        if (event.key === 'Enter') {
            scanCoinBtn.click();
        }
    });
    
    // Koyu/Açık mod geçişi
    darkModeToggle.addEventListener('click', toggleDarkMode);
}

// Filtre ayarlarını al
function getFilterSettings() {
    return {
        exchange_id: exchangeSelect.value,
        lookback_bars: parseInt(lookbackInput.value),
        selected_timeframe: timeframeSelect.value // Seçilen zaman dilimini ekle
    };
}

// Sinyalleri yükle
function loadSignals() {
    fetch('/get_signals')
        .then(response => response.json())
        .then(data => {
            signals = data.signals || [];
            updateSignalTable();
            updateSignalCount(signals.length);
        })
        .catch(error => {
            console.error('Sinyaller yüklenirken hata:', error);
            updateStatus('Sinyaller yüklenirken hata oluştu');
        });
}

// Sinyal ekle - HİZLI VERSİYON - tekli satır ekleme - daha hızlı performans
function addSignal(signal) {
    //console.log('Sinyal ekleniyor:', signal);
    
    // FORMAT KONTROLÜ: entry_signals varsa - sinyal yükleme formatı farklı
    if (signal.entry_signals && Array.isArray(signal.entry_signals)) {
        // Bu format farklı bir sinyal formatı - içindeki sinyalleri ekle
        signal.entry_signals.forEach(entry => {
            const formattedSignal = {
                symbol: signal.symbol || '',
                timeframe: signal.timeframe || '',
                peak_price: entry.peak_price || 0,
                peak_time: entry.peak_time || '-',
                low_buy: entry.low_buy || 0,
                medium_buy: entry.medium_buy || 0,
                high_buy: entry.high_buy || 0,
                peak_distance: entry.peak_distance || 0,
                timestamp: typeof entry.timestamp === 'string' ? entry.timestamp : new Date().toISOString()
            };
            
            // Format uyumluluğunu sağla
            if (typeof formattedSignal.peak_price !== 'number') {
                formattedSignal.peak_price = parseFloat(formattedSignal.peak_price) || 0;
            }
            
            // Her bir sinyali hızlıca ekle
            addSignal(formattedSignal);
        });
        
        return; // Bu noktada return et, alt kısım tek sinyaller için
    }
    
    // Her zaman gerekli alanların var olduğundan emin ol
    if (!signal.symbol || !signal.timeframe) {
        //console.warn('Eksik sinyal verisi:', signal);
        return;
    }
    
    // Sinyalin temel alanlarını kontrol et ve düzelt
    const cleanSignal = {
        symbol: signal.symbol,
        timeframe: signal.timeframe,
        peak_price: parseFloat(signal.peak_price) || 0,
        peak_time: signal.peak_time || '-',
        low_buy: parseFloat(signal.low_buy) || 0,
        medium_buy: parseFloat(signal.medium_buy) || 0,
        high_buy: parseFloat(signal.high_buy) || 0,
        peak_distance: parseInt(signal.peak_distance) || 0,
        timestamp: signal.timestamp || new Date().toISOString()
    };
    
    // Zaten eklenmiş mi kontrol et - hızlı kontrol
    const exists = signals.some(s => 
        s.symbol === cleanSignal.symbol && 
        s.timeframe === cleanSignal.timeframe && 
        Math.abs(s.peak_price - cleanSignal.peak_price) < 0.00001 // Yaklaşık kontrol
    );
    
    if (!exists) {
        // Sinyali listeye ekle
        signals.push(cleanSignal);
        
        // PERFORMANS İYİLEŞTİRMESİ - tüm tabloyu yenilemek yerine sadece tek satır ekle
        addSignalToTable(cleanSignal);
        updateSignalCount(signals.length);
    }
}

// Tabloya tek bir sinyal ekle - HİZLI EKLEME 
// Tüm tabloyu yeniden çizmek yerine sadece yeni satır ekleme
function addSignalToTable(signal) {
    // Format fonksiyonları
    const formatPrice = (price) => {
        if (price === undefined || price === null) return '-';
        try {
            const numPrice = parseFloat(price);
            return isNaN(numPrice) ? '-' : numPrice.toFixed(6);
        } catch (e) {
            return '-';
        }
    };
    
    // Seçilen zaman dilimini kontrol et ve filtrele
    const selectedTimeframe = timeframeSelect.value;
    if (selectedTimeframe !== 'all' && signal.timeframe !== selectedTimeframe) {
        return; // Seçilen zaman dilimine uymuyorsa ekleme
    }
    
    // Boş tablo mesajını kaldır
    const noDataRows = signalTable.querySelectorAll('.no-data');
    if (noDataRows.length > 0) {
        signalTable.innerHTML = ''; // Boş mesaj varsa temizle
    }
    
    // Yeni satır oluştur
    const row = document.createElement('tr');
    row.className = 'signal-row'; // CSS için sınıf ekle
    
    // Satır içeriğini oluştur
    row.innerHTML = `
        <td>${signal.symbol}</td>
        <td>${signal.timeframe}</td>
        <td>${formatPrice(signal.peak_price)}</td>
        <td>${signal.peak_time}</td>
        <td>${formatPrice(signal.low_buy)}</td>
        <td>${formatPrice(signal.medium_buy)}</td>
        <td>${formatPrice(signal.high_buy)}</td>
        <td>${signal.peak_distance}</td>
    `;
    
    // Satırı tablonun en üstüne ekle (en yeni sinyaller üstte)
    if (signalTable.firstChild) {
        signalTable.insertBefore(row, signalTable.firstChild);
    } else {
        signalTable.appendChild(row);
    }
}

// Sinyal tablosunu güncelle
function updateSignalTable() {
    console.log('Sinyal tablosu güncelleniyor... Toplam sinyal: ' + signals.length);
    
    // Tabloyu temizle
    signalTable.innerHTML = '';
    
    // Sinyaller boş mu kontrol et
    if (!signals || signals.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = `<td colspan="8" class="no-data">Henüz sinyal bulunamadı. Tarama başlatın veya bekleyin.</td>`;
        signalTable.appendChild(row);
        return;
    }
    
    try {
        // Sinyalleri tarih sırasına göre sırala (en yeni üstte)
        const sortedSignals = [...signals].sort((a, b) => {
            // Timestamp formatını düzenle ve tarih oluştur
            let dateA, dateB;
            
            try {
                dateA = new Date(a.timestamp.replace(' ', 'T'));
                if (isNaN(dateA.getTime())) dateA = new Date(); // Geçersizse şu anki zaman
            } catch (e) {
                dateA = new Date(); // Hata olursa şu anki zaman
            }
            
            try {
                dateB = new Date(b.timestamp.replace(' ', 'T'));
                if (isNaN(dateB.getTime())) dateB = new Date();
            } catch (e) {
                dateB = new Date();
            }
            
            return dateB - dateA; // Yeniden eskiye sırala
        });
        
        // Seçilen zaman dilimini al
        const selectedTimeframe = timeframeSelect.value;
        
        // Filtreleme yap - tümü seçiliyse tüm sinyalleri göster
        const filteredSignals = selectedTimeframe === 'all' ? 
            sortedSignals : 
            sortedSignals.filter(signal => signal.timeframe === selectedTimeframe);
        
        console.log(`Filtrelenen sinyal sayısı: ${filteredSignals.length} / ${signals.length}`);
        
        // Sonuç yoksa bilgi mesajı göster
        if (filteredSignals.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = `<td colspan="8" class="no-data">Seçilen zaman diliminde sinyal bulunamadı</td>`;
            signalTable.appendChild(row);
            return;
        }
        
        // Fiyatları formatla (6 basamak)
        const formatPrice = (price) => {
            if (price === undefined || price === null) return '-';
            try {
                const numPrice = parseFloat(price);
                return isNaN(numPrice) ? '-' : numPrice.toFixed(6);
            } catch (e) {
                return '-';
            }
        };
        
        // Her sinyal için tablo satırı oluştur
        filteredSignals.forEach((signal, index) => {
            const row = document.createElement('tr');
            row.dataset.index = index;
            
            // Satıra tıklanınca seçili yap
            row.addEventListener('click', () => {
                selectSignal(index, signal);
            });
            
            // Hücreleri oluştur - yeni tablo yapısına göre
            row.innerHTML = `
                <td>${signal.symbol || '-'}</td>
                <td>${signal.timeframe || '-'}</td>
                <td>${formatPrice(signal.peak_price)}</td>
                <td>${signal.peak_time || '-'}</td>
                <td>${formatPrice(signal.low_buy)}</td>
                <td>${formatPrice(signal.medium_buy)}</td>
                <td>${formatPrice(signal.high_buy)}</td>
                <td>${signal.peak_distance || '-'}</td>
            `;
            
            signalTable.appendChild(row);
        });
        
        // Güncellendiğini konsola bildir
        console.log(`Tablo başarıyla güncellendi. ${filteredSignals.length} satır eklendi.`);
        
    } catch (error) {
        console.error('Tablo güncellenirken hata:', error);
        // Hata durumunda basit bir mesaj göster
        const row = document.createElement('tr');
        row.innerHTML = `<td colspan="8" class="no-data">Tablo güncellenirken hata oluştu. Lütfen sayfayı yenileyin.</td>`;
        signalTable.appendChild(row);
    }
}

// Sinyal seç
function selectSignal(index, signal) {
    // Tüm seçimleri temizle
    const rows = signalTable.querySelectorAll('tr');
    rows.forEach(row => row.classList.remove('selected'));
    
    // Seçilen satırı işaretle
    const selectedRow = signalTable.querySelector(`tr[data-index="${index}"]`);
    if (selectedRow) {
        selectedRow.classList.add('selected');
    }
}

// Tarama durumunu güncelle
function setScanning(scanning) {
    isScanning = scanning;
    
    if (scanning) {
        startScanBtn.style.display = 'none';
        stopScanBtn.style.display = 'flex';
    } else {
        startScanBtn.style.display = 'flex';
        stopScanBtn.style.display = 'none';
    }
}

// Durum mesajını güncelle
function updateStatus(message) {
    statusMessage.textContent = message;
}

// İlerleme çubuğunu güncelle
function updateProgress(progress) {
    progressBar.style.width = `${progress}%`;
}

// Sinyal sayısını güncelle
function updateSignalCount(count) {
    signalCount.textContent = count;
}

// Son tarama zamanını güncelle
function updateLastScanTime() {
    const now = new Date();
    const timeString = now.toLocaleString('tr-TR');
    lastScanTime.textContent = timeString;
}

// Güncel zamanı göster (her saniye güncelle)
function updateCurrentTime() {
    const now = new Date();
    setInterval(() => {
        // Saati güncellemek için gerekirse burada kod eklenebilir
    }, 1000);
}

// Koyu/Açık mod geçişi
function toggleDarkMode() {
    const isDark = document.body.classList.toggle('light-mode');
    darkModeToggle.innerHTML = isDark ? '<i class="fas fa-sun"></i>' : '<i class="fas fa-moon"></i>';
    
    // Tercihi kaydet
    localStorage.setItem('darkMode', !isDark);
}

// Tema tercihini yükle
function loadThemePreference() {
    const prefersDark = localStorage.getItem('darkMode') === 'true';
    
    if (!prefersDark) {
        document.body.classList.add('light-mode');
        darkModeToggle.innerHTML = '<i class="fas fa-sun"></i>';
    }
}
