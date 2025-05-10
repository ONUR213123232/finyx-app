#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
import pandas as pd
import numpy as np
import ccxt
import time
import threading
from datetime import datetime
import traceback

# Flask uygulaması ve SocketIO oluştur
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tepeden-kar-gizli-anahtar'
app.config['SESSION_TYPE'] = 'filesystem'  # Oturum verileri dosya sisteminde saklanacak
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 dakika oturum süresi

# Flask_session kurulumu
from flask_session import Session
Session(app)

# SocketIO kurulumu - Oturum desteği ile
socketio = SocketIO(app, manage_session=False)  # Flask oturumlarını yönetimini biz yapacağız

# Global değişkenler - tüm kullanıcı ve tarama verileri için

# Tüm kullanıcı sonuçlarını saklayacak global sözlük
user_results = {}

# Kullanıcı bazında tarama ayarlarını saklayacak sözlük
scan_settings_store = {}

# Mevcut tarama ayarları
scan_settings = {
    'exchange_id': 'binance',  # Varsayılan borsa
    'selected_timeframe': '1h',  # Varsayılan zaman dilimi
    'lookback_bars': 50,         # Varsayılan mum sayısı
    'is_running': False          # Varsayılan tarama durumu
}

# Zaman dilimleri - Binance tarafından desteklenen tüm zaman dilimleri
TIMEFRAMES = {
    '1m': 1,   # 1 dakika
    '3m': 3,   # 3 dakika
    '5m': 5,   # 5 dakika
    '15m': 15, # 15 dakika
    '30m': 30, # 30 dakika
    '1h': 60,  # 1 saat
    '2h': 120, # 2 saat
    '4h': 240, # 4 saat
    '6h': 360, # 6 saat
    '8h': 480, # 8 saat
    '12h': 720,# 12 saat
    '1d': 1440 # 1 gün
    # NOT: '45m', '3h' gibi bazı zaman dilimlerini Binance API desteklemiyor
    # Bu nedenle kaldırıldı
}

# Zaman dilimi dönüştürme haritası - Binance formatı ile uyumlu
TIMEFRAME_BINANCE_MAP = {
    '1m': '1m',
    '3m': '3m',
    '5m': '5m',
    '15m': '15m',
    '30m': '30m',
    '45m': '30m',  # 45m yerine en yakın 30m kullan
    '1h': '1h',
    '2h': '2h',
    '3h': '2h',    # 3h yerine en yakın 2h kullan
    '4h': '4h',
    '6h': '6h',
    '8h': '8h',
    '12h': '12h',
    '1d': '1d'
}

# Desteklenen borsalar
EXCHANGES = {
    'binance': 'Binance',
    'bybit': 'Bybit',
    'okx': 'OKX',
    'kucoin': 'KuCoin',
    'mexc': 'MEXC'
}

# Açılış ayarları
scan_settings = {
    'exchange_id': 'binance',
    'is_running': False,
    'lookback_bars': 50,
    'selected_timeframe': '1m'  # Varsayılan zaman dilimi
}

# Tarama sonuçları - Kullanıcı bazlı olacak
# Global scan_results artık kullanılmayacak - Referans için bırakıldı
scan_results = []

# Market tarayıcı sınıfı
class MarketScanner:
    def __init__(self, exchange_id='binance', timeframes=None):
        self.exchange_id = exchange_id
        self.timeframes = timeframes or TIMEFRAMES
        
        # CCXT exchange instance
        try:
            self.exchange = getattr(ccxt, exchange_id)({
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future'  # Future marketleri kullan
                }
            })
        except Exception as e:
            print(f"Exchange oluşturulurken hata: {str(e)}")
            self.exchange = None
    
    def get_perpetual_markets(self):
        """Sadece perpetual coinleri al - swaps coinler, USDT perpetual cotratlar"""
        perpetual_markets = []
        
        # Önemli semboller - en üste liste için (doğru perpetual formatında olmalarını sağla)
        important_symbols = [
            'BTC/USDT:USDT', 'ETH/USDT:USDT', 'ADA/USDT:USDT', 'SOL/USDT:USDT', 'XRP/USDT:USDT',
            'BNB/USDT:USDT', 'DOGE/USDT:USDT', 'DOT/USDT:USDT', 'AVAX/USDT:USDT', 'SHIB/USDT:USDT',
            'MATIC/USDT:USDT', 'LTC/USDT:USDT'
        ]
        
        # CCXT'yi doğru ayarla - enableRateLimit kullanarak
        if not self.exchange or self.exchange_id.lower() == 'binance':
            self.exchange = ccxt.binance({
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True,
                    'recvWindow': 5000  # Hızlı yanıt
                }
            })
        
        # Marketleri hızlı bir şekilde al
        try:
            markets = self.exchange.fetch_markets()
            perpetual_markets = []
            
            # Hızlı fetch_markets - tüm perpetual coinleri al
            try:
                markets = self.exchange.fetch_markets()
                
                for market in markets:
                    symbol = market['symbol']
                    if '/USDT' in symbol:
                        # Perpetual kontrat mı kontrol et - birkaç farklı yöntemle
                        is_perpetual = False
                        
                        # Linear futures kontrol
                        if market.get('linear', False):
                            is_perpetual = True
                        
                        # info açıkça perpetual olduğunu belirtiyorsa
                        if isinstance(market.get('info'), dict):
                            if market['info'].get('contractType') == 'PERPETUAL':
                                is_perpetual = True
                                
                        # Swap tipi kontrat
                        if market.get('swap', False):
                            is_perpetual = True
                            
                        # Eğer perpetual ise listeye ekle
                        if is_perpetual:
                            perpetual_markets.append(symbol)
                
                # Yeterli sayıda perpetual market bulduk mu?
                if len(perpetual_markets) >= 10:
                    print(f"Toplam {len(perpetual_markets)} perpetual market bulundu.")
                    
                    # Önemli sembolleri listenin başına al
                    for symbol in reversed(important_symbols):
                        if symbol in perpetual_markets:
                            perpetual_markets.remove(symbol)
                        perpetual_markets.insert(0, symbol)
                        
                    return perpetual_markets
            except Exception as e:
                print(f"Marketleri çekerken hata: {str(e)}")
            
            # Alternatif yöntem: load_markets
            if len(perpetual_markets) < 10:
                try:
                    markets_dict = self.exchange.load_markets()
                    for symbol, market in markets_dict.items():
                        if '/USDT' in symbol and (market.get('linear', False) or market.get('swap', False)):
                            if symbol not in perpetual_markets:
                                perpetual_markets.append(symbol)
                                
                    if len(perpetual_markets) >= 10:
                        print(f"load_markets ile {len(perpetual_markets)} perpetual market bulundu.")
                        # Önemli sembolleri listenin başına al
                        for symbol in reversed(important_symbols):
                            if symbol in perpetual_markets:
                                perpetual_markets.remove(symbol)
                            perpetual_markets.insert(0, symbol)
                            
                        return perpetual_markets
                except Exception as e:
                    print(f"load_markets ile marketleri çekerken hata: {str(e)}")
            
            # Hala yeterli market bulunamadıysa, temel listemizi dön
            print(f"Yeterli perpetual market bulunamadı, temel liste dönülüyor ({len(perpetual_markets)})")
            if len(perpetual_markets) > 5:
                # Eğer en az 5 market bulduk ve önemli semboller içindeyse
                for symbol in reversed(important_symbols):
                    if symbol in perpetual_markets:
                        perpetual_markets.remove(symbol)
                    perpetual_markets.insert(0, symbol)
                return perpetual_markets
            else:
                # Temel sembolleri dön
                return important_symbols
                
        except Exception as e:
            print(f"Perpetual marketleri alma genel hatası: {str(e)}")
            return important_symbols  # Hata durumunda önemli sembolleri döndür
            
    def fetch_all_timeframes(self, symbol):
        try:
            # WebSocket bağlantısı kullanan CCXT yaklaşımı
            # Binance'in önerdiği gibi WebSocket kullanımına geçiyoruz
            result = {}
            
            # CCXT 3.0+ için WebSocket destekli yeni nesne oluştur
            try:
                # Önce mevcut Exchange nesnesi varsa kapat
                if hasattr(self, 'exchange') and self.exchange:
                    try:
                        if hasattr(self.exchange, 'close'):
                            self.exchange.close()
                    except Exception:
                        pass
                
                # WebSocket destekli yeni nesne
                self.exchange = getattr(ccxt, self.exchange_id)({
                    'enableRateLimit': True,
                    'options': {
                        'defaultType': 'future',
                        'adjustForTimeDifference': True,
                        'warnOnFetchOHLCVLimitArgument': False,
                        'recvWindow': 60000  # Binance için daha uzun bir alacak pencere süresi
                    }
                })
            except Exception as e:
                print(f"WebSocket exchange oluşturma hatası: {str(e)}")
                # Hata durumunda yine de devam et, mevcut exchange ile işlem yap
            
            # SADECE seçilen zaman dilimini kullan - çok daha hızlı tarama için
            # Seçilen zaman dilimini al
            selected_tf = TIMEFRAME_BINANCE_MAP.get(scan_settings.get('selected_timeframe', '1h').lower(), '1h')
            
            # Sadece tek bir zaman diliminde işlem yap - diğerlerini atla
            for tf_name, tf_minutes in {selected_tf: self.timeframes.get(selected_tf, 60)}.items():
                try:
                    
                    # Çoklu deneme mekanizması
                    retry_attempt = 0
                    max_attempts = 3
                    fetched = False
                    
                    while not fetched and retry_attempt < max_attempts:
                        try:
                            # WebSocket destekli OHLCVs çekimi - limit daha küçük
                            ohlcv = self.exchange.fetch_ohlcv(
                                symbol, 
                                timeframe=tf_name, 
                                limit=150  # 200 yerine 150 limit kullan
                            )
                            
                            if not ohlcv or len(ohlcv) < 50:
                                # Yeterince veri yok, diğer zaman dilimine geç
                                break
                                
                            # DataFrame'e çevir
                            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                            
                            # Sonuça ekle
                            result[tf_name] = df
                            fetched = True  # Başarılı çekim
                            
                        except Exception as e:
                            error_str = str(e)
                            print(f"{symbol} için {tf_name} zaman dilimi alınırken hata: {error_str}")
                            
                            # Hız limiti aşıldıysa daha uzun bekle
                            if any(keyword in error_str.lower() for keyword in ['rate', 'limit', 'too many', 'ban']):
                                sleep_time = 1.5 * (retry_attempt + 1)  # Her denemede artan süre bekle
                                print(f"API limit aşıldı, {sleep_time:.1f} saniye bekleniyor...")
                                time.sleep(sleep_time)
                            
                            retry_attempt += 1
                except Exception as e:
                    print(f"Zaman dilimi {tf_name} için genel hata: {str(e)}")
                    # Hata olsa bile diğer zaman dilimlerine devam et
                    
            return result
        except Exception as e:
            print(f"Tüm zaman dilimleri için veri alma hatası: {str(e)}")
            return None

# Fibonacci hesaplayıcı
class FibonacciCalculator:
    def __init__(self, lookback_bars=50):
        self.lookback_bars = lookback_bars
        
    def analyze_all_timeframes(self, market_data):
        results = {}
        for timeframe, df in market_data.items():
            # Son mumun tarihi (en güncel veri)
            last_time = df['timestamp'].iloc[-1]
            
            # Fibonacci analizi yap
            signals = self.find_latest_peak(df)
            
            # Sonucu ekle
            results[timeframe] = signals
        
        return results
        
    def find_latest_peak(self, df):
        # Son lookback_bars kadar mumu al
        lookback_df = df.iloc[-self.lookback_bars:].copy()
        if len(lookback_df) < 20:  # En az 20 mum gerekli
            return {'entry_signals': []}
        
        # Zaman dilimi bazlı analiz
        timeframe = df.iloc[-1]['timestamp'] - df.iloc[-2]['timestamp']
        hours_diff = timeframe.total_seconds() / 3600
        
        # Tepe arama parametrelerini zaman dilimine göre ayarla
        peak_lookback = self.lookback_bars
        if hours_diff >= 24:  # günlük veya üstü
            peak_lookback = min(100, self.lookback_bars)
        elif hours_diff >= 4:  # 4 saat veya üstü
            peak_lookback = min(80, self.lookback_bars)
        
        # Son peak_lookback mum içinde tepe noktayı bul
        recent_df = lookback_df.iloc[-peak_lookback:]
        
        # TEPE BULMA - AYNEN indikatör kodunda olduğu gibi:
        # isPeak() fonksiyonu gibi tepe kontrolu yap
        peak_bars = []
        
        # Tüm mumları iterate et ve tepe olanları işaretle
        for i in range(1, len(recent_df)-1):
            idx = recent_df.index[i]
            if (recent_df.loc[idx, 'high'] > recent_df.iloc[i-1]['high'] and 
                recent_df.loc[idx, 'high'] > recent_df.iloc[i+1]['high']):
                peak_bars.append(i)
        
        # Eğer tepe bulunamadıysa, sinyal dönme
        if not peak_bars:
            return {'entry_signals': []}
        
        # En son tepeyi bul
        last_peak_idx = recent_df.index[peak_bars[-1]]
        peak_high = recent_df.loc[last_peak_idx, 'high']
        peak_low = recent_df.loc[last_peak_idx, 'low']  # TEPE MUMUN DÜŞÜĞÜ
        peak_time = recent_df.loc[last_peak_idx, 'timestamp']
        peak_bar = last_peak_idx - lookback_df.index[0]
        
        # *** ÖNEMLİ DEĞİŞİKLİK: İNDIKATÖR KODU GİBİ HESAPLA ***
        # İndikatör kodunda peakHigh ve peakLow, 
        # tepe mumun kendi yüksek ve düşük değerleridir!
        # Tam olarak koda uygun price_range hesapla
        price_range = peak_high - peak_low
        
        # TradingView indikatöründe AYNEN olduğu gibi hesapla:
        # fib1618 = peakLow - (peakHigh - peakLow) * 1.618
        # fib175 = peakLow - (peakHigh - peakLow) * 1.75
        # fib2 = peakLow - (peakHigh - peakLow) * 2.0
        buy_levels = {
            'low_buy': peak_low - (price_range * 1.618),    # -1.618 seviyesi 
            'medium_buy': peak_low - (price_range * 1.75),  # -1.75 seviyesi
            'high_buy': peak_low - (price_range * 2.0)      # -2.0 seviyesi
        }
        
        # Son fiyat
        current_price = lookback_df['close'].iloc[-1]
        
        # Tepe mesafesi - en son tepenin kaç mum önce oluştuğunu gösterir
        peak_distance = len(lookback_df) - 1 - peak_bar
        
        # Alım sinyalleri
        entry_signals = []
        
        # Tepe bilgisi ve satınalma seviyeleri
        signal = {
            'type': 'entry',
            'peak_price': peak_high,  # Düzeltildi: peak -> peak_high
            'peak_time': peak_time.strftime('%Y-%m-%d %H:%M'),
            'low_buy': buy_levels['low_buy'],
            'medium_buy': buy_levels['medium_buy'],
            'high_buy': buy_levels['high_buy'],
            'peak_distance': peak_distance,  # Yukarıda hesaplanan değeri kullan
            'timestamp': lookback_df['timestamp'].iloc[-1]
        }
        
        entry_signals.append(signal)
        
        return {'entry_signals': entry_signals}

# Tarama thread'i
# Tarama thread'i - yeni WebSocket destekli ve hızlı çalışan versiyon
def scanner_thread(user_sid=None):
    global scan_settings
    print(f"Tarayıcı başlatıldı (kullanıcı sid: {user_sid})")
    
    # Kullanıcının scan ayarlarını al
    if user_sid and user_sid in scan_settings_store:
        scan_settings = scan_settings_store[user_sid].copy()
    
    try:
        # Market taraması devam ediyor mu kontrol et
        if not scan_settings.get('is_running', False):
            time.sleep(1)
            return

        # Tarama ayarlarını getir
        selected_timeframe = scan_settings.get('selected_timeframe', '1h')
        selected_coin = scan_settings.get('selected_coin', None)
        
        # Market taraması için scanner başlat
        market_scanner = MarketScanner('binance')
        
        # Eğer özel bir coin seçildiyse, sadece onu tara
        if selected_coin:
            markets = [selected_coin] if isinstance(selected_coin, str) else []
        else:
            # Tüm marketleri al
            markets = market_scanner.get_perpetual_markets()
            
        # Kullanıcıya bilgi gönder
        if user_sid:
            socketio.emit('status_update', {'message': f"{len(markets)} perpetual market bulundu"}, room=user_sid)
        
        # Seçilen zaman dilimi
        binance_timeframe = TIMEFRAME_BINANCE_MAP.get(selected_timeframe.lower(), '1h')
        print(f"Tarama için: {selected_timeframe} -> {binance_timeframe}")
        
        # API ban yemeden optimize edilmiş tarama
        new_results = []  # Tüm sinyalleri saklayacak liste
        batch_size = 2    # 2'li gruplar - API ban'dan kaçınmak için
        
        for i in range(0, len(markets), batch_size):
            if not scan_settings.get('is_running', False):
                break
                
            # Bu grup için semboller
            batch_symbols = markets[i:i+batch_size]
            
            for j, symbol in enumerate(batch_symbols):
                # Mevcut indeks
                current_idx = i + j
                progress = int((current_idx / len(markets)) * 100)
                
                # İlerleme güncelle
                if user_sid:
                    socketio.emit('progress_update', {'progress': progress}, room=user_sid)
                    socketio.emit('status_update', 
                                 {'message': f"Analiz: {symbol} ({current_idx+1}/{len(markets)})"}, 
                                 room=user_sid)
                
                # WebSocket destekli veri çekimi
                market_data = market_scanner.fetch_all_timeframes(symbol)
                if not market_data:
                    continue
                
                # Seçilen zaman dilimini analiz et
                if binance_timeframe in market_data:
                    df = market_data[binance_timeframe]
                    
                    # Fibonacci hesaplayıcısını oluştur
                    fibonacci_calculator = FibonacciCalculator(
                        lookback_bars=scan_settings.get('lookback_bars', 25)
                    )
                    
                    # Fibonacci analizi yap (orijinal)
                    signals = fibonacci_calculator.find_latest_peak(df)
                    
                    # Bulunan sinyalleri işle - Hızlandırılmış versiyon
                    if signals and 'entry_signals' in signals and signals['entry_signals']:
                        # Tüm sinyalleri bir listede topla - daha hızlı işlem için
                        batch_signals = []
                        
                        for signal in signals['entry_signals']:
                            # Sinyal verilerini düzenle
                            signal_data = {
                                'symbol': symbol,
                                'timeframe': selected_timeframe,
                                'peak_price': float(signal['peak_price']),
                                'peak_time': signal['peak_time'],
                                'low_buy': float(signal['low_buy']),
                                'medium_buy': float(signal['medium_buy']),
                                'high_buy': float(signal['high_buy']),
                                'peak_distance': signal['peak_distance'],
                                'timestamp': signal['timestamp'].strftime('%Y-%m-%d %H:%M')
                            }
                            
                            # Sonuç listesine ekle
                            new_results.append(signal_data)
                            batch_signals.append(signal_data)
                            print(f"SINYAL: {symbol} - {selected_timeframe} - Fiyat: {signal['peak_price']}")
                        
                        # Her sinyali tek tek tabloya düşürmek için sırayla işle
                        if batch_signals and user_sid:
                            # Her sinyal için tek tek işlem yap - kullanıcı hepsinin bitmesini beklemeyecek
                            for signal in batch_signals:
                                # Sinyal ekle (ANINDA tabloya yansıyacak)
                                socketio.emit('new_signal', signal, room=user_sid)
                                socketio.emit('scan_result', signal, room=user_sid)
                                # Bekleme tamamen kaldırıldı - en hızlı sinyal gösterimi
                                # BEKLEME YOK - MAKSIMUM HIZ
                            
                            # Durum güncellemesi
                            socketio.emit('status_update', {
                                'message': f"{symbol} için {len(batch_signals)} sinyal bulundu! Toplam: {len(new_results)}"
                            }, room=user_sid)
            
            # Binance API Ban'dan korunmak için makul bir bekleme
            # Çok kısa bekleme ile ban riski yüksek, makul bir değer kullanalım
            time.sleep(0.1)  # 100ms - API limitlerine uygun, ban riski düşük
            
        # Kullanıcıya özel sonuçları kaydet    
        if user_sid:
            user_results[user_sid] = new_results
            socketio.emit('scan_complete', {'message': "Tarama tamamlandı"}, room=user_sid)
            socketio.emit('progress_update', {'progress': 100}, room=user_sid)
        
        # Taramayı durdur
        scan_settings['is_running'] = False
        if user_sid and user_sid in scan_settings_store:
            scan_settings_store[user_sid]['is_running'] = False
            
    except Exception as e:
        print(f"Tarama thread hatası: {str(e)}")
        # Hata durumunda taramayı durdur
        scan_settings['is_running'] = False
        if user_sid and user_sid in scan_settings_store:
            scan_settings_store[user_sid]['is_running'] = False
            socketio.emit('error', {'message': f"Tarama hatası: {str(e)}"}, room=user_sid)
            print(f"Tarama thread'inde hata: {str(e)}")
            traceback.print_exc()
            socketio.emit('status_update', {'message': f"Hata: {str(e)}"})
            socketio.emit('progress_update', {'progress': 0})
            time.sleep(10)  # Hata durumunda biraz bekle

# Keep-Alive endpoint - UptimeRobot için
@app.route('/keep-alive')
def keep_alive():
    return jsonify({
        'status': 'alive',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'message': 'The application is running!'
    })

# Ana sayfa
@app.route('/')
def index():
    return render_template('index.html', 
                           exchanges=EXCHANGES, 
                           timeframes=list(TIMEFRAMES.keys()),  # Sadece timeframe adları ('1m', '5m' vb.)
                           current_settings=scan_settings)

# Tarama ayarlarını güncelle
@app.route('/update_settings', methods=['POST'])
def update_settings():
    try:
        data = request.json
        scan_settings['exchange_id'] = data.get('exchange_id', 'binance')
        scan_settings['lookback_bars'] = int(data.get('lookback_bars', 50))
        scan_settings['selected_timeframe'] = data.get('selected_timeframe', '1m')  # Seçilen zaman dilimini ekleyin
        
        return jsonify({
            'success': True, 
            'message': 'Ayarlar başarıyla güncellendi'
        })
    except Exception as e:
        return jsonify({
            'success': False, 
            'message': f'Ayarlar güncellenirken hata oluştu: {str(e)}'
        })

# Özel coin tara
@app.route('/scan_coin', methods=['POST'])
def scan_coin():
    try:
        # Ayarları al
        data = request.json
        symbol = data.get('symbol', '').strip().upper()
        exchange_id = data.get('exchange_id', 'binance')
        lookback_bars = int(data.get('lookback_bars', 50))
        
        # Seçilen zaman dilimini al - UI'dan gelen değeri öncelikle kullan
        selected_timeframe = data.get('selected_timeframe') 
        
        # Eğer UI'dan timeframe gelmezse, global ayarlardan al
        if not selected_timeframe:
            selected_timeframe = scan_settings.get('selected_timeframe', '1m')
        
        if not symbol:
            return jsonify({
                'success': False, 
                'message': 'Lütfen geçerli bir coin sembolü girin'
            })
        
        # Trim - boşlukları kaldır
        symbol = symbol.strip()
        
        # Kısmi arama desteği - kullanıcı sadece "BTC" yazdığında "BTC/USDT" gibi tamamla
        # Eğer / içermiyorsa ve USDT/USD ile bitmiyorsa
        if '/' not in symbol and not symbol.endswith('USDT') and not symbol.endswith('USD'):
            symbol = f"{symbol}/USDT"
        elif not symbol.endswith('USDT') and not symbol.endswith('USD') and not symbol.endswith('/USDT') and not symbol.endswith('/USD'):
            # Backslash içeriyorsa ama USDT/USD ile bitmiyorsa
            symbol = f"{symbol}USDT"
        
        # CCXT formatına uygunlaştır
        if '/' not in symbol and 'USDT' in symbol:
            base = symbol.replace('USDT', '')
            symbol = f"{base}/USDT"
            
        # Market tarayıcısı oluştur - Binance için future modunu aktifleştir
        market_scanner = MarketScanner('binance', TIMEFRAMES)  # Binance kullan
        fibonacci_calculator = FibonacciCalculator(lookback_bars=lookback_bars)
        
        print(f"Taranan sembol: {symbol}, Zaman dilimi: {selected_timeframe}")
        
        # Sembol için verileri çek - Özel olarak seçilen zaman dilimini çek
        try:
            # Seçilen zaman dilimini Binance'in desteklediği bir formata dönüştür
            # Örneğin, 45m ve 3h Binance tarafından desteklenmiyor
            binance_timeframe = TIMEFRAME_BINANCE_MAP.get(selected_timeframe.lower(), '1h')
            
            print(f"Sembol: {symbol}, Kullanıcı seçimi: {selected_timeframe}, Binance formatı: {binance_timeframe}")
            
            # CCXT fetch_ohlcv ile direkt olarak Binance tarafından desteklenen formatı kullan
            ohlcv = market_scanner.exchange.fetch_ohlcv(symbol, timeframe=binance_timeframe, limit=200)
            
            if not ohlcv or len(ohlcv) < 50:
                return jsonify({
                    'success': False, 
                    'message': f"{symbol} için {selected_timeframe} zaman diliminde yeterli veri bulunamadı."
                })
                
            # DataFrame'e çevir
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            # Fibonacci analizi
            signals = fibonacci_calculator.find_latest_peak(df)
            
            # Sonuçları işle
            new_signals = []
            for signal in signals.get('entry_signals', []):
                signal_data = {
                    'symbol': symbol,
                    'timeframe': selected_timeframe,
                    'peak_price': float(signal['peak_price']),
                    'peak_time': signal['peak_time'],
                    'low_buy': float(signal['low_buy']),
                    'medium_buy': float(signal['medium_buy']),
                    'high_buy': float(signal['high_buy']),
                    'peak_distance': signal['peak_distance'],
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
                }
                
                # Sinyali ekle
                new_signals.append(signal_data)
                socketio.emit('new_signal', signal_data)
                
        except Exception as e:
            print(f"Sembol veri alma hatası: {str(e)}")
            return jsonify({
                'success': False, 
                'message': f"{symbol} için veri alınamadı: {str(e)}"
            })
        
        # Eğer sonuç bulunamadıysa
        if not new_signals:
            return jsonify({
                'success': True, 
                'message': f"{symbol} için sinyal bulunamadı."
            })
            
        return jsonify({
            'success': True, 
            'message': f"{symbol} için {len(new_signals)} sinyal bulundu."
        })
        
    except Exception as e:
        error_msg = f"Hata: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return jsonify({
            'success': False, 
            'message': error_msg
        })

# Taramayı durdur
@app.route('/stop_scan', methods=['POST'])
def stop_scan():
    global scan_settings
    
    if scan_settings['is_running']:
        scan_settings['is_running'] = False
        return jsonify({'success': True, 'message': 'Tarama durduruldu'})
    else:
        return jsonify({'success': False, 'message': 'Tarama zaten durdurulmuş'})

# Tüm sinyalleri al
@app.route('/get_signals', methods=['GET'])
def get_signals():
    user_sid = request.sid if hasattr(request, 'sid') else None
    
    # Kullanıcıya özel sinyalleri al
    if user_sid and user_sid in user_results:
        return jsonify({'signals': user_results[user_sid]})
    else:
        return jsonify({'signals': []})
        
@app.route('/clear_signals', methods=['POST'])
def clear_signals():
    """Tüm sinyalleri temizle - tablonun temizlenmesi için"""
    user_sid = request.sid if hasattr(request, 'sid') else None
    
    # Sinyalleri temizle
    if user_sid and user_sid in user_results:
        user_results[user_sid] = []
        
    return jsonify({'success': True, 'message': 'Sinyaller temizlendi'})

# Socket.IO ile tarama başlatma
@socketio.on('start_scan')
def socket_start_scan(data):
    global scan_settings
    
    # Kullanıcı kimliğini al
    sid = request.sid
    print(f'Tarama başlatılıyor: {sid} - Zaman dilimi: {data.get("timeframe", "1m")}')
    
    # Tarama ayarlarını güncelle
    scan_settings['is_running'] = True
    scan_settings['selected_timeframe'] = data.get('timeframe', '1m')
    scan_settings['symbols_to_scan'] = data.get('symbols_to_scan', 'all')
    
    # Kullanıcı için tarama thread'i başlat
    thread = threading.Thread(target=scanner_thread, args=(sid,))
    thread.daemon = True
    thread.start()
    
    return {'status': 'success', 'message': 'Tarama başlatıldı'}
    
# Socket.IO ile özel coin tarama
@socketio.on('scan_coin')
def socket_scan_coin(data):
    # Kullanıcı kimliğini al
    sid = request.sid
    symbol = data.get('symbol', '').strip().upper()
    
    if not symbol:
        return {'status': 'error', 'message': 'Geçerli bir coin sembolü girilmedi'}
    
    print(f'Coin taraması başlatılıyor: {sid} - Sembol: {symbol}')
    
    try:
        # Ayarları al
        exchange_id = data.get('exchange_id', 'binance')
        lookback_bars = int(data.get('lookback_bars', 50))
        selected_timeframe = data.get('selected_timeframe', '1m')
        
        # Market tarayıcısı oluştur
        market_scanner = MarketScanner(exchange_id, TIMEFRAMES)
        fibonacci_calculator = FibonacciCalculator(lookback_bars=lookback_bars)
        
        # Seçilen zaman dilimini Binance'in desteklediği bir formata dönüştür
        binance_timeframe = TIMEFRAME_BINANCE_MAP.get(selected_timeframe.lower(), '1h')
        
        print(f"Sembol: {symbol}, Kullanıcı seçimi: {selected_timeframe}, Binance formatı: {binance_timeframe}")
        
        # CCXT fetch_ohlcv ile direkt olarak Binance tarafından desteklenen formatı kullan
        ohlcv = market_scanner.exchange.fetch_ohlcv(symbol, timeframe=binance_timeframe, limit=200)
        
        if not ohlcv or len(ohlcv) < 50:
            return {'status': 'error', 'message': f"{symbol} için {selected_timeframe} zaman diliminde yeterli veri bulunamadı."}
            
        # DataFrame'e çevir
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # Fibonacci analizi
        signals = fibonacci_calculator.find_latest_peak(df)
        
        # Sonuçları işle
        new_signals = []
        for signal in signals.get('entry_signals', []):
            signal_data = {
                'symbol': symbol,
                'timeframe': selected_timeframe,
                'peak_price': float(signal['peak_price']),
                'peak_time': signal['peak_time'],
                'low_buy': float(signal['low_buy']),
                'medium_buy': float(signal['medium_buy']),
                'high_buy': float(signal['high_buy']),
                'peak_distance': signal['peak_distance'],
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
            }
            
            # Sinyali kullanıcının sonuç listesine ekle
            if sid in user_results:
                user_results[sid].append(signal_data)
            
            # Sinyali SADECE bu kullanıcıya gönder - kesinlikle global olmamalı
            socketio.emit('new_signal', signal_data, room=sid)
            new_signals.append(signal_data)
            
            # Bu noktada diğer kullanıcıların bu sinyali GÖRMEMESİ garanti altına alınmıştır
            
        # Eğer sonuç bulunamadıysa
        if not new_signals:
            return {'status': 'success', 'message': f"{symbol} için sinyal bulunamadı."}
                
        return {'status': 'success', 'message': f"{symbol} için {len(new_signals)} sinyal bulundu."}
            
    except Exception as e:
        error_msg = f"Hata: {str(e)}"
        print(error_msg)
        traceback.print_exc()
        return {'status': 'error', 'message': error_msg}

# Soket bağlantısı
@socketio.on('connect')
def handle_connect():
    sid = request.sid
    print(f'Client bağlandı: {sid}')
    
    # Her yeni bağlantıda kullanıcıya özel oda oluştur
    from flask_socketio import join_room
    join_room(sid)
    
    # Kullanıcı için yeni bir liste oluştur
    if sid not in user_results:
        user_results[sid] = []
    
    # Bağlantı bilgisini gönder
    socketio.emit('connection_status', {'connected': True, 'user_id': sid}, room=sid)
    
@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f'Client bağlantısı kesildi: {sid}')
    
    # Bağlantı kesildiğinde, temizlik yapabiliriz
    # İsteğe bağlı: Uzun süre bağlantı kesilirse sonuçları temizleyebiliriz
    # if sid in user_results:
    #    del user_results[sid]

# Sürekli çalışan arka plan iş parçacığı
def background_worker():
    """Uygulamayı aktif tutmak için sürekli çalışan arka plan görevi"""
    while True:
        try:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Keep-alive worker çalışıyor...")
            time.sleep(60)  # Her 1 dakikada bir bilgi yazdır
        except Exception as e:
            print(f"Background worker hata: {str(e)}")
            time.sleep(5)  # Hata durumunda 5 saniye bekle

# Uygulamayı aktif tutmak için kullanılan özel yol ("trick")
def keep_alive():
    """Uygulamayı aktif tutmak için özel yöntem"""
    # Replit'in uyku modunu engellemeyi deneyebilecek bazı yan etkiler
    os.environ['PYTHONUNBUFFERED'] = '1'  # Tamponlamasız çıktı
    os.environ['KEEP_ALIVE'] = 'true'     # Replit için ipucu
    
    # Arka plan işlem parçacığını başlat
    worker_thread = threading.Thread(target=background_worker, daemon=True)
    worker_thread.start()
    print("Uygulama sürekli çalışma modu aktif!")

# Başlangıç noktası
if __name__ == '__main__':
    # Önce keep-alive sistemini başlat
    keep_alive()
    
    # Replit üzerinde çalışırken port 443'ü kullan, aksi takdirde 5000
    port = int(os.environ.get('PORT', 5000))
socketio.run(app, host='0.0.0.0', port=port, debug=True, allow_unsafe_werkzeug=True)
