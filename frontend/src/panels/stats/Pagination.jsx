import { useEffect, useMemo, useState } from 'react';
import { T } from '../../tokens.js';

const PAGE_SIZE_DEFAULT = 10;

/**
 * Slice a row list into pages and expose page-state + nav buttons.
 *
 *   const { page, sliced, totalPages, setPage } = usePagination(rows);
 *   return (<>
 *     {sliced.map(...)}
 *     <Pagination page={page} totalPages={totalPages} onChange={setPage}
 *                 totalRows={rows.length} pageSize={10} />
 *   </>);
 *
 * Resets to page 0 when the underlying list shrinks past the current page
 * (e.g. a filter narrowed results below the current offset).
 */
export function usePagination(rows, pageSize = PAGE_SIZE_DEFAULT) {
  const [page, setPage] = useState(0);
  const total = Array.isArray(rows) ? rows.length : 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => {
    if (page > 0 && page >= totalPages) setPage(0);
  }, [totalPages, page]);
  const sliced = useMemo(
    () => (Array.isArray(rows) ? rows.slice(page * pageSize, (page + 1) * pageSize) : []),
    [rows, page, pageSize]
  );
  return { page, setPage, sliced, totalPages, pageSize, total };
}

function NavButton({ disabled, onClick, children, label }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      style={{
        background: 'transparent',
        border: `1px solid ${T.border}`,
        color: disabled ? T.dim : T.sub,
        borderRadius: 5,
        padding: '3px 9px',
        fontFamily: 'JetBrains Mono',
        fontSize: 11,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.4 : 1,
        transition: 'background 0.1s, color 0.1s',
      }}
    >{children}</button>
  );
}

export function Pagination({ page, totalPages, onChange, totalRows, pageSize }) {
  if (totalPages <= 1) return null;
  const start = page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, totalRows);
  return (
    <div
      data-testid="pagination"
      style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '8px 14px', borderTop: `1px solid ${T.border}`,
        fontSize: 10, color: T.dim, fontFamily: 'JetBrains Mono',
      }}
    >
      <span>{start}–{end} of {totalRows}</span>
      <div style={{ flex: 1 }} />
      <NavButton disabled={page === 0} onClick={() => onChange(page - 1)} label="previous page">prev</NavButton>
      <span style={{ color: T.sub }}>{page + 1} / {totalPages}</span>
      <NavButton disabled={page >= totalPages - 1} onClick={() => onChange(page + 1)} label="next page">next</NavButton>
    </div>
  );
}
