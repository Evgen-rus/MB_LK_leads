import { useState, type CSSProperties } from 'react';
import { fetchHistoryEventDetail, type HistoryEventDetail } from '../api';
import ChangeProjectDiffModal from './ChangeProjectDiffModal';
import { formatSourceTextForDisplay } from '../utils/sourceCodeDisplay';

type Props = {
  eventId?: string | null;
  disabled?: boolean;
  label?: string;
  className?: string;
  style?: CSSProperties;
};

function HistoryEventCardButton({
  eventId,
  disabled = false,
  label = 'Карточка',
  className = 'btn btn--secondary',
  style,
}: Props) {
  const [detail, setDetail] = useState<HistoryEventDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleOpen() {
    if (!eventId || loading || disabled) return;
    try {
      setLoading(true);
      setError(null);
      const resp = await fetchHistoryEventDetail(eventId);
      setDetail(resp);
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'message' in err && typeof (err as { message?: unknown }).message === 'string'
          ? (err as { message: string }).message
          : 'Не удалось открыть карточку';
      setError(formatSourceTextForDisplay(msg));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={className}
        style={style}
        onClick={handleOpen}
        disabled={disabled || !eventId || loading}
        title={error || (!eventId ? 'Нет данных карточки' : 'Открыть карточку')}
      >
        {loading ? 'Загрузка…' : label}
      </button>
      {error && (
        <span className="sub" style={{ color: '#d00' }}>
          {error}
        </span>
      )}
      {detail && (
        <ChangeProjectDiffModal
          change={detail}
          onClose={() => setDetail(null)}
          showCopyJson={false}
        />
      )}
    </>
  );
}

export default HistoryEventCardButton;
