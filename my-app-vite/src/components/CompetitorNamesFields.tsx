type CompetitorNamesFieldsProps = {
  phones: string[];
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  disabled?: boolean;
};

function CompetitorNamesFields({ phones, value, onChange, disabled = false }: CompetitorNamesFieldsProps) {
  if (phones.length === 0) return null;

  return (
    <fieldset style={{ display: 'grid', gap: 8, border: 0, padding: 0, margin: 0 }}>
      <legend className="section-title">Названия конкурентов для D</legend>
      <span className="hint">Необязательно. Название будет добавлено к источнику в таблице Google Sheets.</span>
      {phones.map((phone) => (
        <label key={phone} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, auto) 1fr', alignItems: 'center', gap: 8 }}>
          <span>{phone}</span>
          <input
            type="text"
            maxLength={120}
            aria-label={`Название конкурента для телефона ${phone}`}
            value={value[phone] || ''}
            onChange={(event) => onChange({ ...value, [phone]: event.target.value })}
            disabled={disabled}
          />
        </label>
      ))}
    </fieldset>
  );
}

export default CompetitorNamesFields;
