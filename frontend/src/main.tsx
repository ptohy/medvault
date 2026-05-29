
import React, { useEffect, useState } from 'react';
import { ThemeProvider, createTheme, CssBaseline, Paper, Button as MuiButton, Chip, Dialog, DialogTitle, DialogContent, IconButton, Box, Typography } from '@mui/material';
import CloseRoundedIcon from '@mui/icons-material/CloseRounded';
import { createRoot } from 'react-dom/client';
import { Bell, CalendarDays, Check, ChevronRight, ClipboardList, FileText, FlaskConical, HeartPulse, Inbox, Pill, Plus, Settings, ShieldCheck, Syringe, Upload, X, Stethoscope, TestTube2, CalendarCheck2 } from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import './index.css';


const materialTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: '#22d3ee', contrastText: '#041018' },
    secondary: { main: '#a78bfa' },
    error: { main: '#ef4444' },
    warning: { main: '#f59e0b' },
    success: { main: '#10b981' },
    background: {
      default: '#0b0f14',
      paper: '#171b22',
    },
    text: {
      primary: '#f8fafc',
      secondary: '#a1a1aa',
    },
    divider: 'rgba(255,255,255,0.10)',
  },
  shape: { borderRadius: 22 },
  typography: {
    fontFamily: 'Inter, Roboto, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    button: { textTransform: 'none', fontWeight: 700 },
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          borderRadius: 999,
          minHeight: 40,
          paddingLeft: 18,
          paddingRight: 18,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          borderRadius: 28,
          border: '1px solid rgba(255,255,255,0.10)',
          backgroundImage: 'linear-gradient(180deg, rgba(255,255,255,0.055), rgba(255,255,255,0.025))',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 650 },
      },
    },
  },
});

const API_BASE =
  import.meta.env.VITE_API_BASE && import.meta.env.VITE_API_BASE !== 'auto'
    ? import.meta.env.VITE_API_BASE
    : '';

async function api(path: string, options: RequestInit = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
    cache: 'no-store',
  });

  if (!response.ok) {
    const raw = await response.text();
    let detail:any = raw;

    try {
      const parsed = JSON.parse(raw);
      detail = parsed?.detail ?? parsed?.message ?? parsed;
    } catch {}

    let message = '';
    if (typeof detail === 'string') {
      message = detail;
    } else {
      try {
        message = JSON.stringify(detail, null, 2);
      } catch {
        message = String(detail);
      }
    }

    if (message.trim().startsWith('<html')) {
      message = `HTTP ${response.status} em ${path}. O backend respondeu HTML/proxy em vez de JSON.`;
    }

    throw new Error(message || `Erro HTTP ${response.status}`);
  }

  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    const text = await response.text();
    throw new Error(`Resposta inválida em ${path}: esperado JSON, recebido ${contentType || 'sem content-type'}. ${text.slice(0, 300)}`);
  }

  return response.json();
}

function parseDateLike(value: string) {
  if (!value) return null;
  const raw = String(value).trim();
  const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?/);
  if (m) {
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0));
  }
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

function twoDigits(value: number) {
  return String(value).padStart(2, '0');
}

function formatDayMonth(value: string) {
  const d = parseDateLike(value);
  if (!d) return '--/--';
  return `${twoDigits(d.getDate())}/${twoDigits(d.getMonth() + 1)}`;
}

function formatTime(value: string) {
  const d = parseDateLike(value);
  if (!d) return '';
  return `${twoDigits(d.getHours())}:${twoDigits(d.getMinutes())}`;
}

function formatDateTimeCompact(value: string) {
  const d = parseDateLike(value);
  if (!d) return value || '';
  return `${formatDayMonth(value)} · ${formatTime(value)}`;
}

function formatDateCompact(value: string) {
  const d = parseDateLike(value);
  if (!d) return value || '';
  return formatDayMonth(value);
}

function remainingApplications(item: any) {
  const total = Number(item?.supply_total || 0);
  const used = Number(item?.supply_used || 0);
  if (!total) return 0;
  return Math.max(total - used, 0);
}

function sideLabel(side: string) {
  if (!side) return '';
  return side.toLowerCase().includes('direit') ? 'lado direito' : side.toLowerCase().includes('esquerd') ? 'lado esquerdo' : side;
}

function medicationSummary(item: any) {
  const raw = String(item?.treatment_name || item?.name || item?.title || '').trim();
  const lower = raw.toLowerCase();

  if (lower.includes('deposteron')) return 'Aplicação Deposteron';
  if (lower.includes('mounjaro')) return 'Aplicação Mounjaro';
  if (lower.includes('ozempic')) return 'Aplicação Ozempic';
  if (lower.includes('centrum')) return 'Centrum';

  const clean = raw
    .replace(/\([^)]*\)/g, ' ')
    .replace(/\b\d+([,.]\d+)?\s*(mg|mcg|g|ml|ui|iu)\b/gi, ' ')
    .replace(/\bsolu[cç][aã]o\b/gi, ' ')
    .replace(/\binjet[aá]vel\b/gi, ' ')
    .replace(/\bcomprimido[s]?\b/gi, ' ')
    .replace(/\bc[aá]psula[s]?\b/gi, ' ')
    .replace(/\bems\b/gi, ' ')
    .replace(/\bsigma\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  const first = clean.split(/[,-]/)[0]?.trim() || raw;
  const words = first.split(' ').filter(Boolean).slice(0, 3).join(' ');
  const isInjection = lower.includes('injet') || lower.includes('ampola') || lower.includes('caneta') || lower.includes('aplica');
  return `${isInjection ? 'Aplicação ' : ''}${words || 'Medicamento'}`.trim();
}


function eventVisual(event: any) {
  const text = `${event?.title || ''} ${event?.matched_keyword || ''} ${event?.classification_type || ''}`.toLowerCase();
  if (text.includes('exame') || text.includes('laborat') || text.includes('hemograma')) return { icon: TestTube2, className: 'event-type-icon--exam' };
  if (text.includes('consulta') || text.includes('medic') || text.includes('urolog') || text.includes('endocr')) return { icon: Stethoscope, className: 'event-type-icon--consult' };
  if (text.includes('aplica') || text.includes('injet') || text.includes('deposteron') || text.includes('mounjaro')) return { icon: Syringe, className: 'event-type-icon--med' };
  return { icon: HeartPulse, className: 'event-type-icon--default' };
}

function formatWeekday(value: string) {
  const d = parseDateLike(value);
  if (!d) return '';
  return ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'][d.getDay()];
}

function formatMonthShort(value: string) {
  const d = parseDateLike(value);
  if (!d) return '';
  return ['JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN', 'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ'][d.getMonth()];
}

function DateTile({ value, accent=false }: any) {
  return <div className={`material-date-tile ${accent ? 'material-date-tile--accent' : ''}`}>
    <div className="date-weekday">{formatWeekday(value)}</div>
    <div className="date-day">{formatDayMonth(value).slice(0,2)}</div>
    <div className="date-month">{formatMonthShort(value)}</div>
    {formatTime(value) && <div className="date-time">{formatTime(value)}</div>}
  </div>;
}

function eventActionMeta(actionLabel: string) {
  const lower = (actionLabel || '').toLowerCase();
  const isInjection = lower.includes('aplicar');
  return {
    doneAction: isInjection ? 'applied' : 'taken',
    doneLabel: isInjection ? 'Marcar aplicado' : 'Marcar tomado',
    scheduledLabel: 'Confirmar agendamento',
    postponeLabel: 'Adiar 1 dia',
    skipLabel: 'Não realizado'
  };
}

function Card({ children, className = '' }: any) {
  return <Paper elevation={0} component="section" className={`material-card ${className}`}>{children}</Paper>;
}

function Button({ children, className = '', ...props }: any) {
  return <MuiButton variant="contained" color="primary" className={className} {...props}>{children}</MuiButton>;
}

function Secondary({ children, className = '', ...props }: any) {
  return <MuiButton variant="outlined" color="inherit" className={className} {...props}>{children}</MuiButton>;
}

function Danger({ children, className = '', ...props }: any) {
  return <MuiButton variant="contained" color="error" className={className} {...props}>{children}</MuiButton>;
}

function Badge({ children, tone='default' }: any) {
  const colors:any = {
    default: 'default',
    good: 'success',
    warn: 'warning',
    cyan: 'info',
  };
  return <Chip size="small" color={colors[tone] || 'default'} label={children} variant={tone === 'default' ? 'outlined' : 'filled'} />;
}

function SectionTitle({ title, subtitle }: any) {
  return <Box className="section-title">
    <Typography variant="h6" component="h2" className="section-title__heading">{title}</Typography>
    {subtitle && <Typography variant="body2" color="text.secondary" className="section-title__subtitle">{subtitle}</Typography>}
  </Box>;
}

function Empty({ text }: any) {
  return <Paper elevation={0} className="material-empty">{text}</Paper>;
}

function Modal({ title, onClose, children, wide=false }: any) {
  return <Dialog open onClose={onClose} fullWidth maxWidth={wide ? 'lg' : 'md'} scroll="paper">
    <DialogTitle className="material-dialog-title">
      <Typography variant="h6" component="h2">{title}</Typography>
      <IconButton onClick={onClose} size="small" aria-label="Fechar">
        <CloseRoundedIcon fontSize="small" />
      </IconButton>
    </DialogTitle>
    <DialogContent dividers className="material-dialog-content">{children}</DialogContent>
  </Dialog>;
}

function App() {
  const [tab, setTab] = useState('dashboard');
  const [data, setData] = useState<any>({});
  const [status, setStatus] = useState<any>({});
  const [settings, setSettings] = useState<any>({});
  const [error, setError] = useState('');
  const [viewer, setViewer] = useState<any>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [forceManualProfile, setForceManualProfile] = useState(false);
  const [settingsTab, setSettingsTab] = useState('profiles');
  const [chartMarker, setChartMarker] = useState('TSH');
  const [chartData, setChartData] = useState<any[]>([]);
  const [profileEdit, setProfileEdit] = useState<any>(null);
  const [examAction, setExamAction] = useState<any>(null);
  const [processing, setProcessing] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [jobProgress, setJobProgress] = useState<any>(null);
  const [undoBanner, setUndoBanner] = useState<any>(null);
  const [purchaseImportOpen, setPurchaseImportOpen] = useState(false);
  const [purchaseReview, setPurchaseReview] = useState<any>(null);
  const [purchaseProcessing, setPurchaseProcessing] = useState(false);

  async function load() {
    try {
      const [boot, st, cfg] = await Promise.all([api('/api/bootstrap'), api('/api/status'), api('/api/settings')]);
      setData(boot); setStatus(st); setSettings(cfg); setError('');
    } catch (e:any) { setError(e.message); }
  }
  useEffect(() => { load(); }, []);

  const profiles = data.profiles || [];
  const today = data.today || [];
  const treatments = data.treatments || [];
  const prescriptions = data.prescriptions || [];
  const prescriptionItems = data.prescription_items || [];
  const examOrders = data.exam_orders || [];
  const examOrderItems = data.exam_order_items || [];
  const inbox = data.inbox || [];
  const logs = data.logs || [];
  const calendarEvents = data.calendar_events || [];
  const sourceDocuments = data.source_documents || [];
  const inventory = data.inventory || [];
  const inventoryPurchases = data.inventory_purchases || [];


  async function ingest(source: any) {
    try {
      setProcessing(true);
      setUploadProgress(0);
      setJobProgress({ status: 'uploading', progress: 0, stage: 'upload', message: 'Preparando importação.' });
      setError('');

      const isForm = typeof HTMLFormElement !== 'undefined' && source instanceof HTMLFormElement;
      let profileId = '0';
      let sourceUrl = '';
      let qrText = '';
      let files: File[] = [];
      let resetCallback = () => {};

      if (isForm) {
        const fd = new FormData(source);
        profileId = String(fd.get('profile_id') || '0') || '0';
        sourceUrl = String(fd.get('source_url') || '').trim();
        qrText = String(fd.get('qr_text') || '').trim();
        files = Array.from(source.querySelector('input[type="file"]')?.files || []);
      } else {
        profileId = String(source?.profile_id || '0') || '0';
        sourceUrl = String(source?.source_url || '').trim();
        qrText = String(source?.qr_text || '').trim();
        files = Array.from(source?.files || []);
        resetCallback = typeof source?.reset === 'function' ? source.reset : resetCallback;
      }

      const queue: any[] = [];
      files.forEach((file) => queue.push({ type: 'file', file, label: file.name || 'arquivo' }));
      if (sourceUrl) queue.push({ type: 'link', source_url: sourceUrl, label: 'Link Memed/Mevo' });
      if (qrText) queue.push({ type: 'qr', qr_text: qrText, label: 'QRCode' });

      if (!queue.length) {
        throw new Error('Envie ao menos um PDF/imagem, cole um link ou cole o conteúdo do QRCode.');
      }

      const duplicateMessages: string[] = [];

      for (let index = 0; index < queue.length; index++) {
        const item = queue[index];
        const itemPrefix = queue.length > 1 ? `${index + 1}/${queue.length} • ` : '';
        const itemTitle = item.type === 'file' ? `Arquivo: ${item.label}` : item.label;

        setUploadProgress(0);
        setJobProgress({
          status: 'uploading',
          progress: 0,
          stage: 'upload',
          message: `${itemPrefix}Preparando ${itemTitle}`,
        });

        const fd = new FormData();
        fd.set('profile_id', profileId || '0');
        if (item.type === 'file') fd.append('file', item.file);
        if (item.type === 'link') fd.set('source_url', item.source_url);
        if (item.type === 'qr') fd.set('qr_text', item.qr_text);

        const response: any = await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          let gotProgressEvent = false;
          let fallbackTick = 0;

          xhr.open('POST', `${API_BASE}/api/ingest/upload-job`);
          xhr.timeout = 180000;

          const fallbackTimer = window.setInterval(() => {
            if (gotProgressEvent) return;
            fallbackTick = Math.min(85, fallbackTick + 5);
            setUploadProgress(fallbackTick);
            setJobProgress({
              status: 'uploading',
              progress: fallbackTick,
              stage: 'upload',
              message: `${itemPrefix}Enviando ${itemTitle}`,
            });
          }, 1200);

          xhr.upload.onloadstart = () => {
            setUploadProgress(1);
            setJobProgress({ status: 'uploading', progress: 1, stage: 'upload', message: `${itemPrefix}Iniciando upload de ${itemTitle}` });
          };

          xhr.upload.onprogress = (event) => {
            gotProgressEvent = true;
            if (event.lengthComputable && event.total > 0) {
              const pct = Math.max(1, Math.min(99, Math.round((event.loaded / event.total) * 100)));
              setUploadProgress(pct);
              setJobProgress({ status: 'uploading', progress: pct, stage: 'upload', message: `${itemPrefix}Enviando ${itemTitle}: ${pct}%` });
            } else {
              fallbackTick = Math.min(90, fallbackTick + 10);
              setUploadProgress(fallbackTick);
              setJobProgress({ status: 'uploading', progress: fallbackTick, stage: 'upload', message: `${itemPrefix}Enviando ${itemTitle}` });
            }
          };

          xhr.upload.onload = () => {
            gotProgressEvent = true;
            setUploadProgress(100);
            setJobProgress({ status: 'uploading', progress: 100, stage: 'upload', message: `${itemPrefix}Upload concluído: ${itemTitle}` });
          };

          xhr.onload = () => {
            window.clearInterval(fallbackTimer);
            if (xhr.status >= 200 && xhr.status < 300) {
              try { resolve(JSON.parse(xhr.responseText)); }
              catch { reject(new Error('Resposta inválida do servidor.')); }
            } else {
              let msg = xhr.responseText || `Erro HTTP ${xhr.status}`;
              try { msg = JSON.parse(xhr.responseText).detail || msg; } catch {}
              reject(new Error(msg));
            }
          };

          xhr.onerror = () => {
            window.clearInterval(fallbackTimer);
            reject(new Error('Falha de rede no upload. Verifique se o backend está acessível em ' + API_BASE));
          };

          xhr.ontimeout = () => {
            window.clearInterval(fallbackTimer);
            reject(new Error('Timeout no upload. O backend não respondeu a tempo.'));
          };

          xhr.onabort = () => {
            window.clearInterval(fallbackTimer);
            reject(new Error('Upload cancelado.'));
          };

          xhr.send(fd);
        });

        if (!response.job_id) {
          throw new Error('Servidor não retornou job_id.');
        }

        setJobProgress({ status: 'queued', progress: 0, stage: 'queued', message: `${itemPrefix}Processando ${itemTitle}` });

        while (true) {
          const job = await api(`/api/jobs/${response.job_id}`);
          setJobProgress({
            ...job,
            message: job?.message ? `${itemPrefix}${job.message}` : `${itemPrefix}Processando ${itemTitle}`,
          });

          if (job.status === 'done') {
            if (job.result?.duplicate) {
              duplicateMessages.push(`${itemTitle} já havia sido importado (ID ${job.result.source_document_id}).`);
            }
            break;
          }

          if (job.status === 'failed') {
            throw new Error(job.error || `Falha ao processar ${itemTitle}.`);
          }

          await new Promise(r => setTimeout(r, 1200));
        }
      }

      resetCallback();
      await load();
      if (duplicateMessages.length) setError(duplicateMessages.join(' '));

      setTimeout(() => {
        setAddOpen(false);
        setTab('review');
        setProcessing(false);
        setUploadProgress(0);
        setJobProgress(null);
      }, 500);
    } catch (e:any) {
      setError(e.message || 'Falha ao processar documento.');
      setJobProgress({
        status: 'failed',
        progress: 100,
        stage: 'failed',
        message: e.message || 'Falha ao processar documento.'
      });
      setProcessing(false);
    }
  }

  async function actionEvent(event:any, action:string, label:string) {
    const result = await api(`/api/treatment-events/${event.id}/action`, { method:'POST', body: JSON.stringify({ action }) });

    if (action === 'rescheduled') {
      alert(result?.rescheduled ? 'Evento atualizado a partir do calendário.' : (result?.message || 'Nenhum novo agendamento encontrado no calendário.'));
      await load();
      return;
    }

    const undoId = `${event.id}-${Date.now()}`;
    setUndoBanner({ id: undoId, eventId: event.id, label, seconds: 10 });

    let left = 10;
    const timer = window.setInterval(() => {
      left -= 1;
      setUndoBanner((current:any) => {
        if (!current || current.id !== undoId) return current;
        if (left <= 0) {
          window.clearInterval(timer);
          return null;
        }
        return { ...current, seconds: left };
      });
    }, 1000);

    await load();
  }

  async function undoEvent() {
    if (!undoBanner) return;
    await api(`/api/treatment-events/${undoBanner.eventId}/undo`, { method:'POST', body: JSON.stringify({}) });
    setUndoBanner(null);
    await load();
  }

  async function updateTreatmentRules(treatment:any, form:any) {
    const payload:any = Object.fromEntries(new FormData(form));
    payload.current_prescription_id = treatment.current_prescription_id || 0;
    await api(`/api/treatments/${treatment.id}`, { method:'PUT', body: JSON.stringify(payload) });
    load();
  }

  async function addInventoryItem(form:any) {
    const payload:any = Object.fromEntries(new FormData(form));

    const routine = String(payload.routine_preset || 'none');
    const preferredTime = String(payload.preferred_time || '').trim();
    const customFrequency = String(payload.custom_frequency || '').trim();

    payload.requires_prescription = payload.requires_prescription === 'on';
    payload.create_reminder = payload.create_reminder === 'on';

    const routineMap:any = {
      none: { text: '', interval: 0 },
      daily: { text: preferredTime ? `diário às ${preferredTime}` : 'diário', interval: 1 },
      q6h: { text: 'a cada 6 horas', interval: 1 },
      q8h: { text: 'a cada 8 horas', interval: 1 },
      q12h: { text: 'a cada 12 horas', interval: 1 },
      every2d: { text: 'a cada 2 dias', interval: 2 },
      every3d: { text: 'a cada 3 dias', interval: 3 },
      every7d: { text: 'a cada 7 dias', interval: 7 },
      every10d: { text: 'a cada 10 dias', interval: 10 },
      every15d: { text: 'a cada 15 dias', interval: 15 },
      every30d: { text: 'a cada 30 dias', interval: 30 },
      custom: { text: customFrequency, interval: Number(payload.custom_interval_days || 0) || 0 },
    };

    const mapped = routineMap[routine] || routineMap.none;
    payload.default_frequency = mapped.text;
    payload.interval_days = mapped.interval;

    if (mapped.text) {
      payload.create_reminder = true;
    }

    const noteParts = [];
    if (payload.notes) noteParts.push(String(payload.notes));
    if (payload.dose_quantity) noteParts.push(`Baixar ${payload.dose_quantity} ${payload.unit_label || 'unidade'} por dose/aplicação.`);
    if (preferredTime && routine !== 'daily') noteParts.push(`Horário preferido: ${preferredTime}.`);
    payload.notes = noteParts.join('\n');

    delete payload.custom_frequency;
    delete payload.custom_interval_days;

    await api('/api/inventory', { method:'POST', body: JSON.stringify(payload) });
    form.reset();
    load();
  }

  async function addInventoryPurchase(item:any) {
    const quantity = prompt(`Quantidade comprada de ${item.medication_name}:`, '1');
    if (!quantity) return;
    const total_price = prompt('Valor total pago, opcional:', '');
    const vendor = prompt('Local da compra, opcional:', '');
    await api(`/api/inventory/${item.id}/purchase`, {
      method:'POST',
      body: JSON.stringify({
        quantity,
        total_price: total_price || 0,
        vendor: vendor || '',
        purchase_date: new Date().toISOString().slice(0, 10)
      })
    });
    load();
  }


  async function previewInventoryPurchase(form:any) {
    try {
      setPurchaseProcessing(true);
      setError('');
      const fd = new FormData(form);
      const preview = await api('/api/inventory/purchase-preview', { method:'POST', body: fd });
      setPurchaseReview(preview);
    } catch (e:any) {
      setError(e.message || 'Falha ao ler nota/print da farmácia.');
    } finally {
      setPurchaseProcessing(false);
    }
  }

  function updatePurchaseReviewItem(index:number, patch:any) {
    setPurchaseReview((prev:any) => {
      if (!prev) return prev;
      const items = [...(prev.items || [])];
      items[index] = { ...items[index], ...patch };
      return { ...prev, items };
    });
  }

  async function confirmInventoryPurchaseImport() {
    if (!purchaseReview) return;
    try {
      setPurchaseProcessing(true);
      await api('/api/inventory/purchase-confirm', {
        method:'POST',
        body: JSON.stringify({
          profile_id: purchaseReview.profile_id,
          vendor: purchaseReview.vendor || '',
          purchase_date: purchaseReview.purchase_date || new Date().toISOString().slice(0,10),
          items: purchaseReview.items || [],
        })
      });
      setPurchaseImportOpen(false);
      setPurchaseReview(null);
      await load();
    } catch (e:any) {
      setError(e.message || 'Falha ao importar compra para o estoque.');
    } finally {
      setPurchaseProcessing(false);
    }
  }

  async function deleteInventoryItem(item:any) {
    if (!confirm(`Excluir ${item.medication_name} do estoque?`)) return;
    await api(`/api/inventory/${item.id}`, { method:'DELETE' });
    load();
  }

  async function markExamPerformed(order:any, form:any) {
    const payload:any = Object.fromEntries(new FormData(form));
    await api(`/api/exam-orders/${order.id}/performed`, { method:'POST', body: JSON.stringify(payload) });
    setExamAction(null);
    load();
  }

  async function setExamResultDate(order:any, form:any) {
    const payload:any = Object.fromEntries(new FormData(form));
    await api(`/api/exam-orders/${order.id}/result-expected`, { method:'POST', body: JSON.stringify(payload) });
    setExamAction(null);
    load();
  }

  async function markExamResultAvailable(order:any) {
    await api(`/api/exam-orders/${order.id}/result-received`, { method:'POST', body: JSON.stringify({}) });
    setExamAction(null);
    load();
    setAddOpen(true);
  }

  async function scheduleExamOrder(order:any, form:any) {
    const payload:any = Object.fromEntries(new FormData(form));
    await api(`/api/exam-orders/${order.id}/schedule`, { method:'POST', body: JSON.stringify(payload) });
    setExamAction(null);
    load();
  }

  async function resetExamPending(order:any) {
    if (!confirm('Marcar este pedido como pendente novamente?')) return;
    await api(`/api/exam-orders/${order.id}/reset-pending`, { method:'POST' });
    load();
  }

  async function deleteExamOrder(order:any) {
    if (!confirm('Excluir completamente esta importação? Isso remove o PDF enviado por engano e tudo que foi criado a partir dele.')) return;
    await api(`/api/exam-orders/${order.id}/full-import`, { method:'DELETE' });
    load();
  }

  async function deleteSourceDocument(doc:any) {
    if (!confirm('Excluir completamente esta importação? Isso remove o PDF enviado, documentos derivados, receitas, pedidos/resultados de exame e pendências criadas por ela.')) return;
    await api(`/api/source-documents/${doc.id}`, { method:'DELETE' });
    load();
  }

  async function reprocessSourceDocument(doc:any) {
    if (!confirm('Reprocessar OCR/IA deste documento? Isso cria novo job de processamento.')) return;
    const r = await api(`/api/source-documents/${doc.id}/reprocess`, { method:'POST' });
    alert(`Reprocessamento iniciado. Job: ${r.job_id}`);
    load();
  }

  async function resolveInboxItem(item:any, status='reviewed') {
    await api(`/api/inbox/${item.id}/resolve`, {
      method:'POST',
      body: JSON.stringify({ status })
    });
    await load();
  }

  function inboxPayloadSummary(item:any) {
    try {
      const payload = JSON.parse(item.payload_json || '{}');
      const ai = payload.ai || {};
      const keys = Object.keys(ai || {}).filter(Boolean).slice(0, 6);
      if (keys.length) {
        return keys.map(k => `${k}: ${typeof ai[k] === 'object' ? JSON.stringify(ai[k]).slice(0, 80) : String(ai[k]).slice(0, 80)}`).join(' · ');
      }
      if (payload.text) return String(payload.text).replace(/\s+/g, ' ').slice(0, 220);
    } catch {}
    return 'Sem resumo extraído.';
  }

  async function submitProfile(form:any) {
    try {
      const payload:any = Object.fromEntries(new FormData(form));
      payload.name = String(payload.name || '').trim();
      payload.phone_suffix = String(payload.phone_suffix || '').trim();
      payload.notes = String(payload.notes || '').trim();

      if (!payload.name) {
        throw new Error('Informe o nome do perfil.');
      }

      if (profileEdit?.id) {
        await api(`/api/profiles/${profileEdit.id}`, { method:'PUT', body: JSON.stringify(payload) });
        setProfileEdit(null);
      } else {
        await api('/api/profiles', { method:'POST', body: JSON.stringify(payload) });
        form.reset();
      }

      await load();
    } catch (e:any) {
      setError(e.message || 'Falha ao salvar perfil.');
    }
  }

  async function delProfile(id:number) {
    if (!confirm('Excluir perfil? Só funciona se não houver histórico vinculado.')) return;
    await api(`/api/profiles/${id}`, { method:'DELETE' }); load();
  }

  async function loadChart(marker:string) {
    setChartMarker(marker);
    const r = await api(`/api/exam-chart?marker=${encodeURIComponent(marker)}`);
    setChartData((r.items || []).filter((x:any) => x.value !== null).map((x:any) => ({ date:x.result_date, value:x.value, name:x.normalized_name })));
  }
  useEffect(() => { loadChart(chartMarker); }, []);

  const nav = [
    ['dashboard', 'Dashboard'],
    ['treatments', 'Tratamentos'],
    ['inventory', 'Estoque'],
    ['prescriptions', 'Receitas'],
    ['exams', 'Exames'],
    ['agenda', 'Agenda'],
    ['documents', 'Documentos'],
    ['review', 'Revisão'],
  ];

  const sortedCalendarEvents = [...calendarEvents].sort((a:any, b:any) => {
    const da = parseDateLike(a.starts_at)?.getTime() || 0;
    const db = parseDateLike(b.starts_at)?.getTime() || 0;
    return da - db;
  });

  const nextEvent = status.next_event;

  return <main className="min-h-screen">
    <header className="app-header sticky top-0 z-40">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-3 py-3 md:px-8">
        <div className="flex items-center gap-3">
          <div className="brand-icon"><HeartPulse size={20}/></div>
          <div><h1 className="text-lg font-semibold tracking-tight">MedVault</h1><p className="hidden text-xs text-zinc-500 md:block">HealthOps pessoal</p></div>
        </div>
        <nav className="nav-shell hidden items-center gap-1 lg:flex">
          {nav.map(([id,label]) => <button key={id} onClick={() => setTab(id)} className={`nav-pill ${tab === id ? 'nav-pill--active' : ''}`}>{label}</button>)}
        </nav>
        <div className="flex items-center gap-2">
          <Button className="add-button" onClick={() => { setForceManualProfile(false); setAddOpen(true); }}><Plus size={16}/>Adicionar</Button>
          <Secondary onClick={() => setTab('settings')}><Settings size={16}/></Secondary>
        </div>
      </div>
      <nav className="mobile-nav flex gap-2 overflow-x-auto px-3 pb-3 lg:hidden">
        {nav.map(([id,label]) => <button key={id} onClick={() => setTab(id)} className={`nav-pill whitespace-nowrap ${tab === id ? 'nav-pill--active' : ''}`}>{label}</button>)}
      </nav>
    </header>

    <section className="app-main mx-auto max-w-7xl px-3 py-5 md:px-8">
      {error && <Card className="mb-5 border-red-400/20 bg-red-400/10 text-red-200">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <b>Falha ao processar</b>
            <p className="mt-1 text-sm">{error}</p>
          </div>
          {String(error).toLowerCase().includes('paciente') && <div className="flex flex-wrap gap-2">
            <Secondary onClick={() => { setTab('settings'); setSettingsTab('profiles'); }}>
              Corrigir perfis
            </Secondary>
            <Button onClick={() => { setForceManualProfile(true); setAddOpen(true); }}>
              Tentar escolhendo perfil
            </Button>
          </div>}
        </div>
      </Card>}

      {undoBanner && <Card className="mb-5 border-cyan-400/20 bg-cyan-400/10 text-cyan-100">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <b>Ação registrada</b>
            <p className="mt-1 text-sm text-cyan-100/80">{undoBanner.label}. Você pode desfazer por {undoBanner.seconds}s.</p>
          </div>
          <Secondary onClick={undoEvent}>Desfazer</Secondary>
        </div>
      </Card>}

      {tab === 'dashboard' && <>
        <div className="premium-dashboard">
          

          <div className="premium-dashboard-grid">
            <div className="premium-left">
              <Card className="next-med-card">
                <h2>Próximo medicamento</h2>
                {nextEvent ? <div className="next-med-body">
                  <DateTile value={nextEvent.scheduled_at || nextEvent.scheduled_for} accent />
                  <div className="med-icon"><Syringe size={42}/></div>
                  <div className="next-med-info">
                    <h3>{medicationSummary(nextEvent)}</h3>
                    <p>{nextEvent.profile_name} · {nextEvent.scheduled_at ? formatDateTimeCompact(nextEvent.scheduled_at) : formatDateCompact(nextEvent.scheduled_for)}</p>
                    <div className="next-med-actions">
                      {nextEvent.administration_side && <Badge tone="cyan">{sideLabel(nextEvent.administration_side)}</Badge>}
                      {nextEvent.prescription_file_name && <Secondary onClick={() => setViewer({ title:`Receita válida · ${nextEvent.treatment_name}`, file:nextEvent.prescription_file_name })}>Receita válida</Secondary>}
                    </div>
                  </div>
                </div> : <div className="premium-empty-inline">
                  <h3>Nada pendente</h3>
                  <p>Sem medicamentos ou aplicações exigindo ação agora.</p>
                </div>}
              </Card>

              <Card className="pending-premium-card">
                <div className="premium-section-header">
                  <div>
                    <h2>Ações pendentes</h2>
                    <p>Conclua, confirme ou reagende apenas o necessário.</p>
                  </div>
                  {!!today.length && <span className="count-pill">{today.length}</span>}
                </div>

                <div className="pending-list-premium">
                  {today.map((e:any) => {
                    const meta = eventActionMeta(e.action_label);
                    return <div key={e.id} className="pending-row-premium">
                      <DateTile value={e.scheduled_at || e.scheduled_for} />
                      <div className="pending-med-icon"><Syringe size={25}/></div>

                      <div className="pending-row-main">
                        <div className="pending-row-top">
                          <div>
                            <h3>{medicationSummary(e)}</h3>
                            <p>{e.profile_name} · {e.scheduled_at ? formatDateTimeCompact(e.scheduled_at) : formatDateCompact(e.scheduled_for)}</p>
                          </div>
                          <ChevronRight className="pending-chevron" size={22}/>
                        </div>

                        <div className="pending-meta-line">
                          {e.administration_side && <Badge tone="cyan">{sideLabel(e.administration_side)}</Badge>}
                          {!!e.supply_total && <span className="subtle-pill">{remainingApplications(e)} restantes</span>}
                        </div>

                        <div className="pending-actions-premium">
                          {e.prescription_file_name && <Secondary onClick={() => setViewer({ title:`Receita válida · ${e.treatment_name}`, file:e.prescription_file_name })}><FileText size={16}/>Abrir receita</Secondary>}
                          <Button onClick={() => actionEvent(e, meta.doneAction, meta.doneLabel)}><Check size={16}/>{meta.doneLabel}</Button>
                          <Secondary onClick={() => actionEvent(e, 'rescheduled', 'Remarcado') }><CalendarCheck2 size={16}/>Remarcado</Secondary>
                        </div>
                      </div>
                    </div>
                  })}
                  {!today.length && <Empty text="Nenhuma ação pendente agora." />}
                </div>
              </Card>
            </div>

            <Card className="events-premium-card">
              <div className="events-premium-header">
                <h2>Próximos eventos de saúde</h2>
                <Secondary onClick={() => setTab('agenda')}>Ver todos</Secondary>
              </div>

              <div className="events-premium-list">
                {sortedCalendarEvents.slice(0, 4).map((e:any) => <div key={e.id} className="event-row-premium">
                  <DateTile value={e.starts_at} />
                  {(() => { const visual = eventVisual(e); const Icon = visual.icon; return <div className={`event-type-icon ${visual.className}`}><Icon size={24}/></div>; })()}
                  <div className="event-row-content">
                    <h3>{e.title}</h3>
                    <p>{formatTime(e.starts_at)} · {e.location || 'Sem local informado'}</p>
                  </div>
                  <ChevronRight size={22}/>
                </div>)}
                {!calendarEvents.length && <Empty text="Configure o calendário em Configurações > Integrações." />}
              </div>

              {!!calendarEvents.length && <Secondary className="full-calendar-button" onClick={() => setTab('agenda')}>Ver calendário completo <CalendarDays size={16}/></Secondary>}
            </Card>
          </div>
        </div>
      </>}

      {tab === 'treatments' && <Card><SectionTitle title="Tratamentos" subtitle="Rotinas ativas, aplicações, regras operacionais e próximas ações." /><div className="grid gap-3 md:grid-cols-2">{treatments.map((t:any) => { const pendingEvent = today.find((e:any) => e.treatment_id === t.id); return <div key={t.id} className="rounded-3xl border border-white/10 bg-white/[0.035] p-4"><div className="flex items-start justify-between gap-3"><div><h3 className="text-lg font-semibold">{t.name}</h3><p className="mt-1 text-sm text-zinc-400">{t.profile_name} · {t.dosage}</p></div><Badge tone={t.active ? 'good' : 'default'}>{t.active ? 'ativo' : 'inativo'}</Badge></div><p className="mt-3 text-sm text-zinc-500">{t.frequency_text || 'Sem frequência definida'}</p><div className="mt-3 flex flex-wrap gap-2">{!!t.supply_total && <Badge tone="warn">restam {remainingApplications(t)} de {t.supply_total}</Badge>}{pendingEvent?.administration_side && <Badge tone="cyan">próximo: {sideLabel(pendingEvent.administration_side)}</Badge>}{t.prescription_status && <Badge>{t.prescription_status}</Badge>}</div>{t.prescription_file_name && <div className="mt-3"><Secondary onClick={() => setViewer({ title:`Receita válida · ${t.name}`, file:t.prescription_file_name })}>Abrir receita válida</Secondary></div>}<form className="mt-4 grid gap-3" onSubmit={(e) => { e.preventDefault(); updateTreatmentRules(t, e.currentTarget); }}><label className="text-sm text-zinc-400">Regra/observação operacional</label><textarea name="rule_notes" defaultValue={t.rule_notes || ''} placeholder="Ex.: Deposteron alterna lado e começa no lado direito. Mounjaro deve ser aplicado no lado oposto da última aplicação do Deposteron." /><div className="flex flex-wrap gap-2"><Button>Salvar regra</Button></div></form></div>})}{!treatments.length && <Empty text="Nenhum tratamento ativo." />}</div></Card>}

      {tab === 'inventory' && <div className="inventory-premium inventory-v680 inventory-v681">
        <div className="page-heading inventory-heading">
          <div>
            <h2>Estoque</h2>
          </div>
          <div className="inventory-heading-actions">
            <div className="inventory-summary">
              <div><strong>{inventory.length}</strong><span>itens</span></div>
              <div><strong>{status.low_stock_items ?? 0}</strong><span>baixo</span></div>
            </div>
            <Button type="button" onClick={() => { setPurchaseImportOpen(true); setPurchaseReview(null); }}>
              <Upload size={16}/>Importar nota/print
            </Button>
          </div>
        </div>

        <Card className="inventory-add-card">
          <div className="inventory-add-layout">
            <div className="inventory-add-copy">
              <span className="inventory-kicker">Novo item</span>
              <h3>Cadastre medicamentos e lembretes</h3>
              <p>Informe o estoque real e escolha uma rotina pronta. O MedVault calcula quando lembrar, quanto baixar do estoque por dose e quando avisar para repor.</p>

              <div className="inventory-examples">
                <button type="button">Deposteron · 1 ampola a cada 10 dias</button>
                <button type="button">Mounjaro · 1 caneta semanal</button>
                <button type="button">Centrum · 1 comprimido diário</button>
              </div>
            </div>

            <form className="inventory-form" onSubmit={(e) => { e.preventDefault(); addInventoryItem(e.currentTarget); }}>
              <div className="smart-form-section">
                <div className="smart-form-section__title">
                  <h3>1. Item em estoque</h3>
                  <p>Cadastre o total disponível em casa.</p>
                </div>

                <div className="inventory-form-grid inventory-form-grid--main">
                  <label className="field-shell">
                    <span>Perfil</span>
                    <select name="profile_id" required>
                      <option value="">Selecione</option>
                      {profiles.map((p:any) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </label>

                  <label className="field-shell field-shell--wide">
                    <span>Medicamento ou vitamina</span>
                    <input name="medication_name" placeholder="Ex.: Deposteron, Mounjaro, Centrum" required />
                  </label>

                  <label className="field-shell">
                    <span>Total disponível</span>
                    <input name="quantity" type="number" step="0.01" placeholder="Ex.: 6" />
                  </label>

                  <label className="field-shell">
                    <span>Formato</span>
                    <select name="unit_label" defaultValue="unidade">
                      <option value="unidade">unidade</option>
                      <option value="comprimido">comprimido</option>
                      <option value="cápsula">cápsula</option>
                      <option value="ampola">ampola</option>
                      <option value="caneta">caneta</option>
                      <option value="frasco">frasco</option>
                      <option value="cartela">cartela</option>
                      <option value="caixa">caixa</option>
                      <option value="sachê">sachê</option>
                      <option value="gota">gota</option>
                      <option value="ml">ml</option>
                      <option value="dose">dose</option>
                    </select>
                  </label>
                </div>

                <div className="inventory-form-grid inventory-form-grid--stock">
                  <label className="field-shell">
                    <span>Baixar por uso</span>
                    <input name="dose_quantity" type="number" step="0.01" placeholder="1" defaultValue="1" />
                  </label>

                  <label className="field-shell">
                    <span>Avisar quando restar</span>
                    <input name="low_stock_threshold" type="number" step="0.01" placeholder="1" defaultValue="1" />
                  </label>

                  <label className="field-shell">
                    <span>Valor total pago</span>
                    <input name="total_price" type="number" step="0.01" placeholder="R$" />
                  </label>

                  <label className="field-shell">
                    <span>Data da compra</span>
                    <input name="purchase_date" type="date" defaultValue={new Date().toISOString().slice(0,10)} />
                  </label>
                </div>
              </div>

              <div className="smart-form-section">
                <div className="smart-form-section__title">
                  <h3>2. Rotina de uso</h3>
                  <p>Escolha uma rotina pronta. Se escolher personalizado, descreva exatamente como deseja o lembrete.</p>
                </div>

                <div className="inventory-form-grid inventory-form-grid--routine-smart">
                  <label className="field-shell">
                    <span>Rotina</span>
                    <select name="routine_preset" defaultValue="none">
                      <option value="none">Somente controlar estoque</option>
                      <option value="daily">Todos os dias</option>
                      <option value="q6h">6 em 6 horas</option>
                      <option value="q8h">8 em 8 horas</option>
                      <option value="q12h">12 em 12 horas</option>
                      <option value="every2d">2 em 2 dias</option>
                      <option value="every3d">3 em 3 dias</option>
                      <option value="every7d">7 em 7 dias</option>
                      <option value="every10d">10 em 10 dias</option>
                      <option value="every15d">15 em 15 dias</option>
                      <option value="every30d">30 em 30 dias</option>
                      <option value="custom">Personalizado</option>
                    </select>
                  </label>

                  <label className="field-shell">
                    <span>Primeiro horário</span>
                    <input name="preferred_time" type="time" />
                  </label>

                  <label className="field-shell field-shell--wide">
                    <span>Personalizado</span>
                    <input name="custom_frequency" placeholder="Ex.: segunda e quinta às 21h; ou a cada 45 dias" />
                  </label>

                  <label className="field-shell">
                    <span>Intervalo em dias</span>
                    <input name="custom_interval_days" type="number" placeholder="opcional" />
                  </label>
                </div>

                <div className="smart-routine-note">
                  <b>Como isso vira lembrete?</b>
                  <span>Rotinas diárias, por intervalo e por hora criam pendências reais no MedVault. Ex.: 6 em 6h gera próximos horários do dia; 7 em 7 dias gera o próximo ciclo.</span>
                </div>
              </div>

              <div className="smart-form-section">
                <div className="smart-form-section__title">
                  <h3>3. Receita e regras</h3>
                  <p>Vincule uma receita quando o medicamento depender dela.</p>
                </div>

                <div className="inventory-form-grid inventory-form-grid--bottom-smart">
                  <label className="field-shell field-shell--wide">
                    <span>Receita vinculada</span>
                    <select name="prescription_id">
                      <option value="">Não vincular</option>
                      {prescriptions.map((r:any) => <option key={r.id} value={r.id}>{r.profile_name} · {r.title} · {r.issue_date || 'sem data'}</option>)}
                    </select>
                  </label>

                  <div className="inventory-toggles inventory-toggles--compact">
                    <label className="toggle-card"><input type="checkbox" name="requires_prescription" /><span>Receita?</span></label>
                    <label className="toggle-card"><input type="checkbox" name="create_reminder" /><span>Lembrete?</span></label>
                  </div>

                  <label className="field-shell field-shell--wide smart-notes">
                    <span>Observações inteligentes</span>
                    <textarea name="notes" placeholder="Ex.: aplicar no lado oposto do Deposteron; tomar após almoço; comprar antes de acabar..." />
                  </label>
                </div>
              </div>

              <div className="inventory-form-actions inventory-form-actions--right">
                <Button type="submit">Adicionar ao estoque</Button>
              </div>
            </form>
          </div>
        </Card>

        <div className="inventory-list-header">
          <div>
            <h3>Itens cadastrados</h3>
            <p>Reposição, estoque baixo e histórico de preço.</p>
          </div>
        </div>

        <div className="inventory-list">
          {inventory.map((item:any) => {
            const purchases = inventoryPurchases.filter((p:any) => p.inventory_id === item.id);
            const last = purchases[0];
            const low = Number(item.units_on_hand || 0) <= Number(item.low_stock_threshold || 0);
            return <Card key={item.id} className={`inventory-item-card ${low ? 'inventory-item-card--low' : ''}`}>
              <div className="inventory-item-top">
                <div>
                  <h3>{item.medication_name}</h3>
                  <p>{item.profile_name} · {item.treatment_name || 'sem lembrete vinculado'}</p>
                </div>
                <Badge tone={low ? 'warn' : 'good'}>{low ? 'repor' : 'ok'}</Badge>
              </div>

              <div className="inventory-stock-box">
                <strong>{Number(item.units_on_hand || 0)}</strong>
                <span>{item.unit_label}</span>
                <small>baixa {Number(item.dose_quantity || 1)} por uso · aviso em ≤ {item.low_stock_threshold}</small>
              </div>

              <div className="inventory-item-chips">
                {item.requires_prescription ? <Badge tone="warn">exige receita</Badge> : <Badge tone="cyan">sem receita</Badge>}
                {item.prescription_title && <Badge>receita vinculada</Badge>}
                {item.default_frequency && <Badge>{item.default_frequency}</Badge>}
              </div>

              {last && <div className="inventory-last-purchase">
                <span>Última compra</span>
                <strong>{formatDateCompact(last.purchase_date)} · R$ {Number(last.unit_price || 0).toFixed(2)}/un.</strong>
              </div>}

              {!!purchases.length && <div className="inventory-price-history">
                {purchases.slice(0,4).map((p:any) => <div key={p.id}>
                  <span>{formatDateCompact(p.purchase_date)}</span>
                  <strong>R$ {Number(p.unit_price || 0).toFixed(2)}</strong>
                </div>)}
              </div>}

              <div className="inventory-card-actions">
                <Button onClick={() => addInventoryPurchase(item)}>Repor</Button>
                <Danger onClick={() => deleteInventoryItem(item)}>Excluir</Danger>
              </div>
            </Card>
          })}

          {!inventory.length && <div className="inventory-empty-state">
            <div className="inventory-empty-icon">+</div>
            <h3>Nenhum item no estoque</h3>
            <p>Cadastre seus medicamentos e vitaminas acima. Ex.: Deposteron, Mounjaro, Centrum.</p>
          </div>}
        </div>
      </div>}

      {tab === 'prescriptions' && <Card><SectionTitle title="Receitas" subtitle="Receitas interpretadas, itens extraídos e PDFs originais." /><div className="space-y-3">{prescriptions.map((r:any) => <div key={r.id} className="rounded-3xl border border-white/10 bg-white/[0.035] p-4"><div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between"><div><h3 className="font-semibold">{r.title}</h3><p className="mt-1 text-sm text-zinc-400">{r.profile_name} · {r.doctor_name} {r.crm} · {r.issue_date}</p><div className="mt-2 flex gap-2"><Badge>{r.status}</Badge>{r.page_range && <Badge>pág. {r.page_range}</Badge>}</div></div>{r.file_name && <Secondary onClick={() => setViewer({title:r.title, file:r.file_name})}>Ver PDF</Secondary>}</div><div className="mt-3 grid gap-2 md:grid-cols-2">{prescriptionItems.filter((i:any) => i.prescription_id === r.id).map((i:any) => <div key={i.id} className="rounded-2xl bg-black/20 p-3"><b>{i.medication_name}</b><p className="mt-1 text-sm text-zinc-400">{i.dosage} · {i.frequency} · {i.duration}</p></div>)}</div></div>)}{!prescriptions.length && <Empty text="Nenhuma receita cadastrada. Use + Adicionar." />}</div></Card>}

      {tab === 'exams' && <div className="space-y-5"><Card><SectionTitle title="Pedidos de exame" subtitle="Pedidos pendentes e históricos separados das receitas." /><div className="space-y-3">{examOrders.map((o:any) => <div key={o.id} className="rounded-3xl border border-white/10 bg-white/[0.035] p-4"><div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between"><div><h3 className="font-semibold">{o.title}</h3><p className="mt-1 text-sm text-zinc-400">{o.profile_name} · {o.doctor_name} · {o.issue_date}</p><div className="mt-2 flex flex-wrap gap-2"><Badge>{o.status}</Badge>{o.page_range && <Badge>pág. {o.page_range}</Badge>}{o.scheduled_at && <Badge tone="cyan">agendado: {o.scheduled_at}</Badge>}{o.performed_at && <Badge tone="good">realizado: {o.performed_at}</Badge>}{o.result_expected_at && <Badge tone="warn">resultado: {o.result_expected_at}</Badge>}</div></div><div className="flex flex-wrap gap-2">
                    {(o.status === 'pending' || o.status === 'scheduled') && <Secondary onClick={() => setExamAction({type:'schedule', order:o})}>{o.status === 'scheduled' ? 'Editar agendamento' : 'Vincular data'}</Secondary>}
                    {(o.status === 'pending' || o.status === 'scheduled') && <Button onClick={() => setExamAction({type:'performed', order:o})}>Exame realizado</Button>}
                    {o.status === 'performed' && <Secondary onClick={() => setExamAction({type:'resultDate', order:o})}>Data do resultado</Secondary>}
                    {o.status === 'performed' && <Button onClick={() => markExamResultAvailable(o)}>Cadastrar resultado</Button>}
                    {o.status === 'result_pending_upload' && <Button onClick={() => setAddOpen(true)}>Enviar resultado</Button>}
                    {o.status !== 'pending' && <Secondary onClick={() => resetExamPending(o)}>Marcar pendente</Secondary>}
                    {o.file_name && <Secondary onClick={() => setViewer({title:o.title, file:o.file_name})}>Ver PDF</Secondary>}
                    <Danger onClick={() => deleteExamOrder(o)}>Excluir tudo</Danger>
                  </div></div><div className="mt-3 flex flex-wrap gap-2">{examOrderItems.filter((i:any) => i.exam_order_id === o.id).map((i:any) => <Badge key={i.id}>{i.normalized_name || i.exam_name}</Badge>)}</div></div>)}{!examOrders.length && <Empty text="Nenhum pedido de exame cadastrado." />}</div></Card><Card><SectionTitle title="Evolução de exames" subtitle="Comparativo por marcador, independente do laboratório." /><div className="mb-4 flex flex-col gap-2 md:flex-row"><input value={chartMarker} onChange={e => setChartMarker(e.target.value)} placeholder="TSH, Vitamina D, Testosterona..." /><Button onClick={() => loadChart(chartMarker)}>Ver gráfico</Button></div><div className="h-72 rounded-3xl bg-black/20 p-4"><ResponsiveContainer width="100%" height="100%"><LineChart data={chartData}><XAxis dataKey="date"/><YAxis/><Tooltip/><Line type="monotone" dataKey="value" stroke="#67e8f9" strokeWidth={2}/></LineChart></ResponsiveContainer></div></Card></div>}

      {tab === 'agenda' && <Card><div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between"><SectionTitle title="Agenda" subtitle="Consultas, exames e eventos de saúde." /><Secondary onClick={async () => { try { const r = await api('/api/calendar/sync', { method:'POST', body: JSON.stringify({}) }); alert(`Calendário sincronizado. Eventos importados: ${r.synced || 0}. Classificador: ${r.classifier || 'local'}.`); await load(); } catch(e:any) { alert(`Falha ao sincronizar calendário: ${e.message}`); } }}>Sincronizar calendário</Secondary></div><div className="grid gap-3 md:grid-cols-2">{sortedCalendarEvents.map((e:any) => <div key={e.id} className="rounded-2xl border border-white/10 bg-white/[0.035] p-4"><div className="flex items-start gap-4"><DateTile value={e.starts_at} /><div><b>{e.title}</b><p className="mt-1 text-sm text-zinc-500">{e.location || 'Sem local informado'}</p><p className="mt-1 text-xs text-zinc-600">Filtro: {e.matched_keyword}</p></div></div></div>)}{!calendarEvents.length && <Empty text="Nenhum evento médico sincronizado." />}</div></Card>}

      {tab === 'documents' && <Card>
        <SectionTitle title="Documentos" subtitle="Importações originais. Use exclusão completa quando enviar um arquivo errado por engano." />
        <div className="mb-5 grid gap-4 md:grid-cols-3">
          <button onClick={() => setTab('prescriptions')} className="text-left">
            <Card className="hover:border-cyan-400/30 hover:bg-cyan-400/5"><FileText/><h3 className="mt-3 font-semibold">Receitas</h3><p className="text-sm text-zinc-400">{prescriptions.length} cadastradas</p><p className="mt-2 text-xs text-cyan-300">Abrir receitas →</p></Card>
          </button>
          <button onClick={() => setTab('exams')} className="text-left">
            <Card className="hover:border-cyan-400/30 hover:bg-cyan-400/5"><FlaskConical/><h3 className="mt-3 font-semibold">Pedidos de exame</h3><p className="text-sm text-zinc-400">{examOrders.length} cadastrados</p><p className="mt-2 text-xs text-cyan-300">Abrir exames →</p></Card>
          </button>
          <button onClick={() => setTab('review')} className="text-left">
            <Card className="hover:border-cyan-400/30 hover:bg-cyan-400/5"><Inbox/><h3 className="mt-3 font-semibold">Revisão</h3><p className="text-sm text-zinc-400">{inbox.length} itens</p><p className="mt-2 text-xs text-cyan-300">Abrir revisão →</p></Card>
          </button>
        </div>

        <div className="space-y-3">
          {sourceDocuments.map((d:any) => <div key={d.id} className="rounded-3xl border border-white/10 bg-white/[0.035] p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <h3 className="font-semibold">{d.title || d.original_name}</h3>
                <p className="mt-1 text-sm text-zinc-400">{d.profile_name || 'perfil não identificado'} · {d.original_name} · {d.created_at}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge>{d.source_type}</Badge>
                  <Badge>{d.status}</Badge>
                  {d.document_date && <Badge>{d.document_date}</Badge>}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {d.file_name && <Secondary onClick={() => setViewer({title:d.title || d.original_name, file:d.file_name})}>Ver PDF</Secondary>}
                <Secondary onClick={() => reprocessSourceDocument(d)}>Reprocessar OCR/IA</Secondary>
                <Danger onClick={() => deleteSourceDocument(d)}>Excluir tudo</Danger>
              </div>
            </div>
          </div>)}
          {!sourceDocuments.length && <Empty text="Nenhum documento original cadastrado." />}
        </div>
      </Card>}

      {tab === 'review' && <Card className="review-premium">
        <SectionTitle title="Central de revisão" subtitle="Documentos que o MedVault não conseguiu classificar automaticamente." />

        <div className="review-list-premium">
          {inbox.map((i:any) => <div key={i.id} className="review-item-premium">
            <div className="review-item-main">
              <div className="review-item-header">
                <div>
                  <h3>{i.source_original_name || i.source_title || i.title || 'Documento médico'}</h3>
                  <p>
                    Perfil: {i.profile_name || 'não informado'} · Tipo detectado: {i.type || 'document'} · Status: {i.status}
                    {i.document_date ? ` · Data: ${formatDateCompact(i.document_date)}` : ''}
                  </p>
                </div>
                <Badge tone={i.status === 'needs_review' ? 'warn' : 'good'}>{i.status === 'needs_review' ? 'revisar' : i.status}</Badge>
              </div>

              <div className="review-explain">
                <b>Por que apareceu aqui?</b>
                <p>O documento foi importado, mas a IA/OCR não teve confiança suficiente para transformar em receita, pedido de exame ou resultado de exame. Ele fica aqui para você conferir, reprocessar ou excluir.</p>
              </div>

              <div className="review-summary">
                <span>Resumo extraído</span>
                <p>{inboxPayloadSummary(i)}</p>
              </div>
            </div>

            <div className="review-actions-premium">
              {i.source_file_name && <Secondary type="button" onClick={() => setViewer({ title: i.source_original_name || i.source_title || 'Documento médico', file: i.source_file_name })}>Ver PDF</Secondary>}
              {i.source_document_id && <Secondary type="button" onClick={() => reprocessSourceDocument({ id: i.source_document_id })}>Reprocessar OCR/IA</Secondary>}
              <Button type="button" onClick={() => resolveInboxItem(i, 'reviewed')}>Marcar revisado</Button>
              {i.source_document_id && <Danger type="button" onClick={() => deleteSourceDocument({ id: i.source_document_id })}>Excluir documento</Danger>}
            </div>
          </div>)}

          {!inbox.length && <Empty text="Nada pendente para revisar." />}
        </div>
      </Card>}

      {tab === 'settings' && <SettingsPage profiles={profiles} logs={logs} status={status} settings={settings} initialSection={settingsTab} profileEdit={profileEdit} setProfileEdit={setProfileEdit} submitProfile={submitProfile} delProfile={delProfile} reload={load} />}
    </section>

    {addOpen && <AddModal onClose={() => setAddOpen(false)} profiles={profiles} ingest={ingest} processing={processing} uploadProgress={uploadProgress} jobProgress={jobProgress} forceManualProfile={forceManualProfile} />}
    {purchaseImportOpen && <PurchaseImportModal
      onClose={() => { setPurchaseImportOpen(false); setPurchaseReview(null); }}
      profiles={profiles}
      review={purchaseReview}
      setReview={setPurchaseReview}
      processing={purchaseProcessing}
      previewInventoryPurchase={previewInventoryPurchase}
      updatePurchaseReviewItem={updatePurchaseReviewItem}
      confirmInventoryPurchaseImport={confirmInventoryPurchaseImport}
    />}
    {examAction && <ExamActionModal
      action={examAction}
      onClose={() => setExamAction(null)}
      markExamPerformed={markExamPerformed}
      setExamResultDate={setExamResultDate}
      markExamResultAvailable={markExamResultAvailable}
      scheduleExamOrder={scheduleExamOrder}
    />}
    {viewer && <Modal title={viewer.title} onClose={() => setViewer(null)} wide><iframe className="pdf-frame" src={`${API_BASE}/uploads/${viewer.file}`} /></Modal>}
  </main>;
}


function PurchaseImportModal({ onClose, profiles, review, setReview, processing, previewInventoryPurchase, updatePurchaseReviewItem, confirmInventoryPurchaseImport }: any) {
  return <Modal title="Importar compra para o estoque" onClose={onClose} wide>
    <div className="purchase-import">
      <div className="purchase-import__intro">
        <div>
          <h3>Nota fiscal, cupom ou print do pedido</h3>
          <p>Envie uma imagem ou PDF da farmácia. O MedVault extrai medicamentos, quantidade, formato e preço para você revisar antes de salvar no estoque.</p>
        </div>
        <div className="purchase-import__badges">
          <span>OCR + IA</span>
          <span>Revisão antes de salvar</span>
        </div>
      </div>

      <form className="purchase-import__form" onSubmit={(e) => { e.preventDefault(); previewInventoryPurchase(e.currentTarget); }}>
        <label className="field-shell">
          <span>Perfil</span>
          <select name="profile_id" required defaultValue="">
            <option value="">Selecione</option>
            {profiles.map((p:any) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>

        <label className="field-shell field-shell--wide">
          <span>Arquivo</span>
          <input type="file" name="file" accept=".pdf,image/*" required />
        </label>

        <Button type="submit" disabled={processing}>{processing ? 'Lendo compra...' : 'Ler nota/print'}</Button>
      </form>

      {review && <div className="purchase-review">
        <div className="purchase-review__header">
          <div>
            <h3>Revise antes de salvar</h3>
            <p>Origem: {review.source || 'OCR'} · Arquivo: {review.original_name || '-'}</p>
          </div>
          <div className="purchase-review__meta">
            <label className="field-shell">
              <span>Farmácia/local</span>
              <input value={review.vendor || ''} onChange={(e) => setReview({ ...review, vendor: e.target.value })} placeholder="Ex.: Drogaria Raia" />
            </label>
            <label className="field-shell">
              <span>Data</span>
              <input type="date" value={review.purchase_date || ''} onChange={(e) => setReview({ ...review, purchase_date: e.target.value })} />
            </label>
          </div>
        </div>

        <div className="purchase-review__items">
          {(review.items || []).map((item:any, index:number) => <div key={index} className="purchase-review-item">
            <label className="purchase-review-item__check">
              <input
                type="checkbox"
                checked={item.import !== false}
                onChange={(e) => updatePurchaseReviewItem(index, { import: e.target.checked })}
              />
            </label>

            <label className="field-shell purchase-review-item__name">
              <span>Medicamento/produto</span>
              <input value={item.name || ''} onChange={(e) => updatePurchaseReviewItem(index, { name: e.target.value })} />
            </label>

            <label className="field-shell">
              <span>Qtd.</span>
              <input type="number" step="0.01" value={item.quantity ?? 1} onChange={(e) => updatePurchaseReviewItem(index, { quantity: e.target.value })} />
            </label>

            <label className="field-shell">
              <span>Formato</span>
              <select value={item.unit_label || 'unidade'} onChange={(e) => updatePurchaseReviewItem(index, { unit_label: e.target.value })}>
                <option value="unidade">unidade</option>
                <option value="comprimido">comprimido</option>
                <option value="cápsula">cápsula</option>
                <option value="ampola">ampola</option>
                <option value="caneta">caneta</option>
                <option value="frasco">frasco</option>
                <option value="cartela">cartela</option>
                <option value="caixa">caixa</option>
                <option value="sachê">sachê</option>
                <option value="ml">ml</option>
                <option value="dose">dose</option>
              </select>
            </label>

            <label className="field-shell">
              <span>Valor</span>
              <input type="number" step="0.01" value={item.total_price ?? 0} onChange={(e) => updatePurchaseReviewItem(index, { total_price: e.target.value })} />
            </label>

            <label className="purchase-review-item__rx">
              <input
                type="checkbox"
                checked={!!item.requires_prescription}
                onChange={(e) => updatePurchaseReviewItem(index, { requires_prescription: e.target.checked })}
              />
              <span>Receita?</span>
            </label>
          </div>)}

          {!(review.items || []).length && <Empty text="Nenhum medicamento identificado. Tente outro print/PDF ou cadastre manualmente." />}
        </div>

        <div className="purchase-review__footer">
          <p>Ao confirmar, o MedVault cria um item novo ou soma a quantidade ao item já existente do mesmo perfil.</p>
          <Button type="button" onClick={confirmInventoryPurchaseImport} disabled={processing || !(review.items || []).length}>
            {processing ? 'Salvando...' : 'Adicionar ao estoque'}
          </Button>
        </div>

        {!!review.raw_text && <details className="purchase-review__raw">
          <summary>Ver texto OCR</summary>
          <pre>{review.raw_text}</pre>
        </details>}
      </div>}
    </div>
  </Modal>;
}


function ExamActionModal({ action, onClose, markExamPerformed, setExamResultDate, markExamResultAvailable, scheduleExamOrder }: any) {
  const order = action.order;
  const today = new Date().toISOString().slice(0, 10);


  if (action.type === 'schedule') {
    return <Modal title="Vincular agendamento do exame" onClose={onClose}>
      <p className="mb-4 text-sm text-zinc-400">
        Informe a data marcada para realização do exame. Isso não escreve no Google Calendar ainda; vincula o pedido a uma data para aparecer na Agenda/Dashboard. A integração escrita com Google Calendar exige OAuth e fica para a próxima etapa.
      </p>
      <form className="grid gap-4" onSubmit={(e) => {e.preventDefault(); scheduleExamOrder(order, e.currentTarget);}}>
        <label className="text-sm text-zinc-400">Data marcada do exame</label>
        <input type="date" name="scheduled_at" defaultValue={order.scheduled_at || today} required />
        <label className="text-sm text-zinc-400">Local/laboratório</label>
        <input name="scheduled_location" defaultValue={order.scheduled_location || ''} placeholder="Ex.: Dasa, Sérgio Franco, laboratório..." />
        <label className="text-sm text-zinc-400">Título no calendário, se já existir</label>
        <input name="scheduled_calendar_title" defaultValue={order.scheduled_calendar_title || ''} placeholder="Ex.: Exames laboratoriais" />
        <Button>Salvar vínculo</Button>
      </form>
    </Modal>;
  }

  if (action.type === 'performed') {
    return <Modal title="Exame realizado" onClose={onClose}>
      <p className="mb-4 text-sm text-zinc-400">Informe quando o exame foi realizado e, se souber, a data prevista para o resultado. Se o resultado não for cadastrado até essa data, o MedVault gera lembrete.</p>
      <form className="grid gap-4" onSubmit={(e) => {e.preventDefault(); markExamPerformed(order, e.currentTarget);}}>
        <label className="text-sm text-zinc-400">Data em que o exame foi realizado</label>
        <input type="date" name="performed_at" defaultValue={today} required />
        <label className="text-sm text-zinc-400">Data prevista do resultado</label>
        <input type="date" name="result_expected_at" />
        <textarea name="notes" placeholder="Observação opcional" />
        <Button>Salvar exame realizado</Button>
      </form>
    </Modal>;
  }

  if (action.type === 'resultDate') {
    return <Modal title="Data do resultado" onClose={onClose}>
      <p className="mb-4 text-sm text-zinc-400">Atualize a data prevista para o resultado. Caso o resultado ainda não tenha sido cadastrado nessa data, um lembrete será disparado.</p>
      <form className="grid gap-4" onSubmit={(e) => {e.preventDefault(); setExamResultDate(order, e.currentTarget);}}>
        <label className="text-sm text-zinc-400">Data prevista do resultado</label>
        <input type="date" name="result_expected_at" defaultValue={order.result_expected_at || today} required />
        <textarea name="notes" placeholder="Observação opcional" defaultValue={order.result_notes || ''} />
        <Button>Salvar data</Button>
      </form>
    </Modal>;
  }

  return <Modal title="Resultado disponível" onClose={onClose}>
    <p className="mb-4 text-sm text-zinc-400">Quando marcar como disponível, o pedido ficará aguardando o upload do laudo/resultado.</p>
    <Button onClick={() => markExamResultAvailable(order)}>Marcar como disponível</Button>
  </Modal>;
}



function AddModal({ onClose, profiles, ingest, processing, uploadProgress, jobProgress, forceManualProfile }: any) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [sourceUrl, setSourceUrl] = useState('');
  const [qrText, setQrText] = useState('');
  const [profileId, setProfileId] = useState('');
  const [dragActive, setDragActive] = useState(false);

  function mergeFiles(fileList: FileList | File[] | null | undefined) {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    setSelectedFiles((prev) => {
      const merged = [...prev];
      incoming.forEach((file) => {
        const exists = merged.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
        if (!exists) merged.push(file);
      });
      return merged;
    });
  }

  function removeFile(target: File) {
    setSelectedFiles((prev) => prev.filter((file) => !(file.name === target.name && file.size === target.size && file.lastModified === target.lastModified)));
  }

  function resetModal() {
    setSelectedFiles([]);
    setSourceUrl('');
    setQrText('');
    setProfileId('');
    setDragActive(false);
  }

  function submitBatch() {
    ingest({
      profile_id: profileId || '0',
      source_url: sourceUrl,
      qr_text: qrText,
      files: selectedFiles,
      reset: resetModal,
    });
  }

  const hasSomething = selectedFiles.length > 0 || sourceUrl.trim().length > 0 || qrText.trim().length > 0;

  return <Modal title="Adicionar ao MedVault" onClose={onClose} wide>
    <div className="add-modal-premium">
      <div className="add-modal-premium__hero">
        <div>
          <h3>Importação inteligente</h3>
          <p>Envie vários PDFs e imagens de uma vez. O MedVault processa cada item individualmente e identifica paciente, documento, datas e status.</p>
        </div>
        <div className="add-modal-premium__hero-pills">
          <span>Múltiplos uploads</span>
          <span>PDF e imagem</span>
          <span>Link ou QRCode</span>
        </div>
      </div>

      {forceManualProfile && <div className="add-modal-premium__warning">
        <b>Correção necessária</b>
        <p>Não consegui identificar o paciente automaticamente. Escolha o perfil correto abaixo para reprocessar o documento.</p>
      </div>}

      <div className="add-modal-premium__layout">
        <div className="add-modal-premium__left">
          <div
            className={`upload-dropzone ${dragActive ? 'is-active' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
            onDragLeave={(e) => { e.preventDefault(); setDragActive(false); }}
            onDrop={(e) => { e.preventDefault(); setDragActive(false); mergeFiles(e.dataTransfer.files); }}
          >
            <div className="upload-dropzone__icon"><Upload size={24} /></div>
            <div className="upload-dropzone__content">
              <strong>Arraste arquivos aqui</strong>
              <p>Ou selecione vários arquivos em lote. Aceita PDF, JPG, PNG e imagens em geral.</p>
            </div>
            <div className="upload-dropzone__actions">
              <button type="button" className="upload-dropzone__button" onClick={() => document.getElementById('medvault-multi-upload')?.click()}>
                Escolher arquivos
              </button>
              <span>{selectedFiles.length ? `${selectedFiles.length} arquivo(s) selecionado(s)` : 'Nenhum arquivo selecionado'}</span>
            </div>
            <input
              id="medvault-multi-upload"
              type="file"
              name="file"
              accept=".pdf,image/*"
              multiple
              hidden
              onChange={(e) => mergeFiles(e.target.files)}
            />
          </div>

          <div className="upload-file-list">
            <div className="upload-file-list__header">
              <strong>Fila de importação</strong>
              <span>{selectedFiles.length ? `${selectedFiles.length} arquivo(s)` : 'Adicione arquivos para começar'}</span>
            </div>
            {selectedFiles.length ? <div className="upload-file-list__items">
              {selectedFiles.map((file) => <div key={`${file.name}-${file.size}-${file.lastModified}`} className="upload-file-item">
                <div className="upload-file-item__meta">
                  <FileText size={18} />
                  <div>
                    <strong>{file.name}</strong>
                    <span>{Math.max(1, Math.round(file.size / 1024))} KB</span>
                  </div>
                </div>
                <button type="button" className="upload-file-item__remove" onClick={() => removeFile(file)} aria-label={`Remover ${file.name}`}>
                  <X size={16} />
                </button>
              </div>)}
            </div> : <div className="upload-file-list__empty">Sem arquivos por enquanto.</div>}
          </div>
        </div>

        <div className="add-modal-premium__right">
          {forceManualProfile && <div>
            <label className="mb-2 block text-sm text-zinc-400">Perfil do paciente</label>
            <select name="profile_id" value={profileId} onChange={(e) => setProfileId(e.target.value)} required>
              <option value="">Selecione o perfil correto</option>
              {profiles.map((p:any) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>}

          <div>
            <label className="mb-2 block text-sm text-zinc-400">Link Memed/Mevo</label>
            <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="Cole aqui o link para baixar o PDF oficial" />
          </div>

          <div>
            <label className="mb-2 block text-sm text-zinc-400">QRCode</label>
            <textarea value={qrText} onChange={(e) => setQrText(e.target.value)} placeholder="Cole aqui o texto ou URL extraído do QRCode" />
          </div>

          <div className="add-modal-premium__helper">
            <p>Você pode combinar arquivos, link e QRCode na mesma importação. Cada item será processado separadamente.</p>
          </div>
        </div>
      </div>

      <div className="add-modal-premium__footer">
        <div className="add-modal-premium__footer-copy">
          <strong>Importação moderna</strong>
          <span>Processamento em lote, visual mais limpo e menos atrito para subir documentos.</span>
        </div>
        <Button type="button" onClick={submitBatch} disabled={processing || !hasSomething} className={`add-modal-premium__submit ${processing || !hasSomething ? 'opacity-60 cursor-not-allowed' : ''}`}>
          <Upload size={16}/>{processing ? 'Processando...' : 'Processar automaticamente'}
        </Button>
      </div>

      {processing && <div className="add-modal-premium__progress">
        <div className="add-modal-premium__progress-head">
          <span>{jobProgress?.message || 'Processando documento...'}</span>
          <span>{jobProgress?.status === 'uploading' ? uploadProgress : (jobProgress?.progress || 0)}%</span>
        </div>
        <div className="add-modal-premium__progress-bar">
          <div
            className="add-modal-premium__progress-bar-fill"
            style={{ width: `${jobProgress?.status === 'uploading' ? uploadProgress : (jobProgress?.progress || 0)}%` }}
          />
        </div>
        <p>Etapa: {jobProgress?.stage || 'upload'}</p>
      </div>}
    </div>
  </Modal>;
}

function SettingsPage({ profiles, logs, status, settings, initialSection, profileEdit, setProfileEdit, submitProfile, delProfile, reload }: any) {
  const [section, setSection] = useState(initialSection || 'profiles');
  useEffect(() => { if (initialSection) setSection(initialSection); }, [initialSection]);
  const [form, setForm] = useState<any>({});

  useEffect(() => {
    setForm({
      ollama_base_url: settings?.ollama_base_url?.value || '',
      ollama_model: settings?.ollama_model?.value || '',
      calendar_ics_url: '',
      ha_webhook_url: '',
      n8n_ingest_token: '',
      share_base_url: settings?.share_base_url?.value || '',
      self_base_url: settings?.self_base_url?.value || '',
    });
  }, [settings]);

  function integrationPayload() {
    const payload:any = {};
    Object.entries(form).forEach(([k,v]:any) => {
      const value = String(v ?? '').trim();
      if (value !== '') payload[k] = value;
    });
    return payload;
  }

  async function saveSettings(e?:any) {
    if (e?.preventDefault) e.preventDefault();
    const payload:any = integrationPayload();

    try {
      const r = await api('/api/settings', { method:'PUT', body: JSON.stringify(payload) });
      await reload();
      alert('Configurações salvas.');
      return r;
    } catch (err:any) {
      alert(`Falha ao salvar integrações: ${err.message}`);
      throw err;
    }
  }

  async function test(target:string) {
    const payload:any = integrationPayload();

    try {
      const r = await api(`/api/settings/test/${target}`, {
        method:'POST',
        body: JSON.stringify(payload)
      });

      if (target === 'calendar') {
        if (r.enabled) {
          alert(`Calendário sincronizado. Eventos importados: ${r.synced || 0}. Classificador: ${r.classifier || 'local'}.`);
        } else {
          alert('Calendário não configurado. Cole a URL ICS e clique em Salvar e sincronizar.');
        }
      } else if (target === 'ha') {
        alert(r.ok ? 'Teste enviado ao Home Assistant.' : `Home Assistant respondeu status ${r.status_code}`);
      } else {
        alert(JSON.stringify(r, null, 2));
      }

      await reload();
      return r;
    } catch (err:any) {
      alert(`Falha no teste/sincronização: ${err.message}`);
      throw err;
    }
  }

  async function syncCalendarDirect() {
    const payload:any = integrationPayload();

    try {
      const r = await api('/api/calendar/sync', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      alert(`Calendário sincronizado. Eventos importados: ${r.synced || 0}. Classificador: ${r.classifier || 'local'}.`);
      await reload();
      return r;
    } catch (err:any) {
      alert(`Falha ao sincronizar calendário: ${err.message}`);
      throw err;
    }
  }

  async function removeSetting(key:string) {
    if (!confirm('Remover configuração?')) return;
    await api(`/api/settings/${key}`, { method:'DELETE' });
    await reload();
  }

  async function resetSystem() {
    const first = confirm('Isto vai apagar TODA a base do MedVault: perfis, receitas, exames, tratamentos, logs, estoque, calendário, uploads e exports. Continuar?');
    if (!first) return;

    const typed = (prompt('Digite RESETAR para confirmar a exclusão completa da base.') || '').trim();
    if (typed !== 'RESETAR') {
      alert('Reset cancelado.');
      return;
    }

    try {
      const result = await api('/api/system/reset', {
        method: 'POST',
        body: JSON.stringify({ confirm: typed })
      });

      alert(`Base resetada com sucesso.\n\nContadores: ${JSON.stringify(result.counters || {}, null, 2)}`);
      window.location.reload();
    } catch (e:any) {
      alert(`Falha ao resetar: ${e.message}`);
      throw e;
    }
  }

  const sections = [['profiles','Perfis'],['integrations','Integrações'],['backup','Backup/exportação'],['logs','Logs'],['system','Sistema']];

  return <Card>
    <SectionTitle title="Configurações" subtitle="Perfis, integrações, IA, calendário, Home Assistant e sistema." />
    <div className="mb-5 flex flex-wrap gap-2">{sections.map(([id,label]) => <button key={id} onClick={() => setSection(id)} className={`rounded-2xl px-3 py-2 text-sm ${section === id ? 'bg-white text-zinc-950' : 'bg-white/[0.06] text-zinc-300'}`}>{label}</button>)}</div>

    {section === 'profiles' && <div className="profiles-premium">
      <div className="profiles-toolbar">
        <div>
          <h3>{profileEdit ? 'Editar perfil' : 'Novo perfil'}</h3>
          <p>Cadastre pacientes e senhas usadas para abrir PDFs protegidos.</p>
        </div>
        {profileEdit && <Secondary type="button" onClick={() => setProfileEdit(null)}>+ Novo perfil</Secondary>}
      </div>

      <form
        key={profileEdit?.id || 'new-profile'}
        className="profile-form-premium"
        onSubmit={(e) => {e.preventDefault(); submitProfile(e.currentTarget);}}
      >
        <label className="field-shell">
          <span>Nome</span>
          <input name="name" placeholder="Ex.: Paulo" defaultValue={profileEdit?.name || ''} required />
        </label>

        <label className="field-shell">
          <span>Senha PDF / sufixo</span>
          <input name="phone_suffix" placeholder="Ex.: 5253" defaultValue={profileEdit?.phone_suffix || ''} />
        </label>

        <label className="field-shell">
          <span>Notas</span>
          <input name="notes" placeholder="Observações do perfil" defaultValue={profileEdit?.notes || ''} />
        </label>

        <div className="profile-form-actions">
          <Button type="submit">{profileEdit ? 'Salvar perfil' : 'Adicionar perfil'}</Button>
          {profileEdit && <Secondary type="button" onClick={() => setProfileEdit(null)}>Cancelar</Secondary>}
        </div>
      </form>

      <div className="profiles-list-premium">
        {profiles.map((p:any) => <div key={p.id} className="profile-row-premium">
          <div>
            <b>{p.name}</b>
            <p>Senha PDF: {p.phone_suffix || '-'}</p>
            {p.notes && <small>{p.notes}</small>}
          </div>
          <div className="profile-row-actions">
            <Secondary type="button" onClick={() => setProfileEdit(p)}>Editar</Secondary>
            <Danger type="button" onClick={() => delProfile(p.id)}>Excluir</Danger>
          </div>
        </div>)}

        {!profiles.length && <Empty text="Nenhum perfil cadastrado. Adicione o primeiro perfil acima." />}
      </div>
    </div>}

    {section === 'integrations' && <form className="grid gap-5" onSubmit={saveSettings}>
      <div className="grid gap-4 md:grid-cols-2">
        <Card><h3 className="font-semibold">Ollama / IA</h3><p className="mb-3 text-sm text-zinc-400">Configure URL e modelo. Atual: {settings?.ollama_model?.value || status.ollama_model}</p><input value={form.ollama_base_url || ''} onChange={e => setForm({...form, ollama_base_url:e.target.value})} placeholder="http://192.168.50.112:11434" /><input className="mt-2" value={form.ollama_model || ''} onChange={e => setForm({...form, ollama_model:e.target.value})} placeholder="qwen2.5:7b" /><div className="mt-3 flex gap-2"><Secondary type="button" onClick={() => test('ollama')}>Testar</Secondary></div></Card>
        <Card><h3 className="font-semibold">URL pública/local do MedVault</h3><p className="mb-3 text-sm text-zinc-400">Usada nos callbacks enviados ao Home Assistant. Se o IP mudar, altere aqui sem mexer no código.</p><input value={form.self_base_url || ''} onChange={e => setForm({...form, self_base_url:e.target.value})} placeholder="http://192.168.50.201:8088" /></Card>
        <Card><h3 className="font-semibold">Calendário ICS</h3><p className="mb-3 text-sm text-zinc-400">Salvo: {settings?.calendar_ics_url?.masked || 'não configurado'}</p><input value={form.calendar_ics_url || ''} onChange={e => setForm({...form, calendar_ics_url:e.target.value})} placeholder="Cole a URL secreta .ics do Google Calendar" /><div className="mt-3 flex gap-2"><Secondary type="button" onClick={syncCalendarDirect}>Salvar e sincronizar</Secondary><Danger type="button" onClick={() => removeSetting('calendar_ics_url')}>Remover</Danger></div></Card>
        <Card><h3 className="font-semibold">Home Assistant</h3><p className="mb-3 text-sm text-zinc-400">Salvo: {settings?.ha_webhook_url?.masked || 'não configurado'}</p><input value={form.ha_webhook_url || ''} onChange={e => setForm({...form, ha_webhook_url:e.target.value})} placeholder="Webhook URL do HA" /><div className="mt-3 flex gap-2"><Secondary type="button" onClick={() => test('ha')}>Enviar teste</Secondary><Danger type="button" onClick={() => removeSetting('ha_webhook_url')}>Remover</Danger></div></Card>
        <Card><h3 className="font-semibold">n8n</h3><p className="mb-3 text-sm text-zinc-400">Endpoint: {settings?.n8n_endpoint}</p><p className="mb-3 text-sm text-zinc-400">Token salvo: {settings?.n8n_ingest_token?.masked || 'não configurado'}</p><input value={form.n8n_ingest_token || ''} onChange={e => setForm({...form, n8n_ingest_token:e.target.value})} placeholder="Token n8n" /><div className="mt-3 flex gap-2"><Danger type="button" onClick={() => removeSetting('n8n_ingest_token')}>Revogar</Danger></div></Card>
      </div>
      <Button type="submit">Salvar integrações</Button>
    </form>}

    {section === 'backup' && <div><p className="mb-3 text-sm text-zinc-400">Exportação manual para migração. Seu backup principal continua sendo o Proxmox.</p><a href={`${API_BASE}/api/export`} className="inline-flex rounded-2xl bg-cyan-400 px-4 py-2 text-sm font-semibold text-zinc-950">Exportar ZIP</a></div>}

    {section === 'logs' && <div className="space-y-2">{logs.map((l:any) => <div key={l.id} className="rounded-2xl bg-white/[0.035] p-3"><b>{l.level} · {l.area}</b><p className="text-sm text-zinc-500">{l.created_at} · {l.message}</p></div>)}</div>}

    {section === 'system' && <div className="space-y-5">
      <Card>
        <h3 className="font-semibold text-red-200">Resetar base de dados</h3>
        <p className="mt-2 text-sm text-zinc-400">
          Apaga completamente perfis, receitas, exames, tratamentos, logs, uploads e recria a base limpa. Use apenas para limpar ambiente de testes.
        </p>
        <div className="mt-4">
          <Danger type="button" onClick={resetSystem}>Resetar tudo</Danger>
        </div>
      </Card>

      <pre className="rounded-2xl bg-black/30 p-4 text-sm">{JSON.stringify(status, null, 2)}</pre>
    </div>}
  </Card>;
}

createRoot(document.getElementById('root')!).render(<ThemeProvider theme={materialTheme}><CssBaseline /><App /></ThemeProvider>);
