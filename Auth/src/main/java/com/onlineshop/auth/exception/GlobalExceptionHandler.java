package com.onlineshop.auth.exception;

import com.onlineshop.auth.dto.ErrorResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.servlet.NoHandlerFoundException;
import org.springframework.web.HttpRequestMethodNotSupportedException;

import jakarta.validation.ConstraintViolationException;
import java.util.stream.Collectors;

@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger logger = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(UserAlreadyExistsException.class)
    public ResponseEntity<ErrorResponse> handleUserAlreadyExistsException(
            UserAlreadyExistsException ex,
            WebRequest request) {
        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/user-already-exists")
                .title("Conflict")
                .status(HttpStatus.CONFLICT.value())
                .detail(ex.getMessage())
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.CONFLICT);
    }

    /**
     * Handles database constraint violations, particularly for duplicate username scenarios
     * that occur during race conditions when two concurrent requests pass the existence check
     * but one fails on insert due to the unique constraint.
     */
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<ErrorResponse> handleDataIntegrityViolationException(
            DataIntegrityViolationException ex,
            WebRequest request) {
        String message = ex.getMostSpecificCause().getMessage();

        // Check if this is a username uniqueness constraint violation
        if (message != null && (message.contains("users_username_key") ||
                message.contains("users_normalized_username_key"))) {
            logger.warn("Duplicate username constraint violation on {}", request.getDescription(false));

            ErrorResponse errorResponse = ErrorResponse.builder()
                    .type("https://api.onlineshop.com/errors/user-already-exists")
                    .title("Conflict")
                    .status(HttpStatus.CONFLICT.value())
                    .detail("A user with this username already exists.")
                    .instance(request.getDescription(false).replace("uri=", ""))
                    .build();

            return new ResponseEntity<>(errorResponse, HttpStatus.CONFLICT);
        }

        // For other data integrity violations, return a generic conflict response
        logger.warn("Data integrity violation on {}: {}", request.getDescription(false), message);

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/conflict")
                .title("Conflict")
                .status(HttpStatus.CONFLICT.value())
                .detail("The request could not be completed due to a conflict with the current state.")
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.CONFLICT);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ErrorResponse> handleValidationException(
            MethodArgumentNotValidException ex,
            WebRequest request) {
        // Collect all validation error messages with field names
        String validationErrors = ex.getBindingResult().getFieldErrors().stream()
                .map(error -> error.getField() + ": " + error.getDefaultMessage())
                .collect(Collectors.joining(", "));

        logger.warn("Validation failed on {}: {}", request.getDescription(false), validationErrors);

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/validation-failed")
                .title("Bad Request")
                .status(HttpStatus.BAD_REQUEST.value())
                .detail("Validation failed: " + validationErrors)
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.BAD_REQUEST);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ErrorResponse> handleIllegalArgumentException(
            IllegalArgumentException ex,
            WebRequest request) {
        logger.warn("Invalid registration input on {}: {}", request.getDescription(false), ex.getMessage());

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/validation-failed")
                .title("Bad Request")
                .status(HttpStatus.BAD_REQUEST.value())
                .detail(ex.getMessage())
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.BAD_REQUEST);
    }

    @ExceptionHandler(InvalidUsernameOrPasswordException.class)
    public ResponseEntity<ErrorResponse> handleInvalidCredentialsException(
            InvalidUsernameOrPasswordException ex,
            WebRequest request) {
        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/invalid-username-or-password")
                .title("Unauthorized")
                .status(HttpStatus.UNAUTHORIZED.value())
                .detail(ex.getMessage())
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.UNAUTHORIZED);
    }

    @ExceptionHandler(MissingAuthorizationHeaderException.class)
    public ResponseEntity<ErrorResponse> handleMissingAuthorizationHeaderException(
            MissingAuthorizationHeaderException ex,
            WebRequest request) {
        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/missing-authorization-header")
                .title("Bad Request")
                .status(HttpStatus.BAD_REQUEST.value())
                .detail(ex.getMessage())
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.BAD_REQUEST);
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ErrorResponse> handleMissingRequestHeaderException(
            MissingRequestHeaderException ex,
            WebRequest request) {
        String headerName = ex.getHeaderName();

        // For Authorization header, return 400 Bad Request
        if ("Authorization".equalsIgnoreCase(headerName)) {
            logger.warn("Missing Authorization header on {}", request.getDescription(false));

            ErrorResponse errorResponse = ErrorResponse.builder()
                    .type("https://api.onlineshop.com/errors/missing-authorization-header")
                    .title("Bad Request")
                    .status(HttpStatus.BAD_REQUEST.value())
                    .detail("Authorization header is required.")
                    .instance(request.getDescription(false).replace("uri=", ""))
                    .build();

            return new ResponseEntity<>(errorResponse, HttpStatus.BAD_REQUEST);
        }

        // For other headers, return 400 Bad Request
        logger.warn("Missing required header '{}' on {}", headerName, request.getDescription(false));

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/missing-header")
                .title("Bad Request")
                .status(HttpStatus.BAD_REQUEST.value())
                .detail("Required header '" + headerName + "' is missing.")
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.BAD_REQUEST);
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<ErrorResponse> handleConstraintViolationException(
            ConstraintViolationException ex,
            WebRequest request) {
        String violations = ex.getConstraintViolations().stream()
                .map(violation -> violation.getPropertyPath() + ": " + violation.getMessage())
                .collect(Collectors.joining(", "));

        // Check if it's an Authorization header violation
        boolean isAuthViolation = ex.getConstraintViolations().stream()
                .anyMatch(v -> v.getPropertyPath().toString().contains("authHeader"));

        if (isAuthViolation) {
            logger.warn("Authorization header validation failed on {}: {}",
                    request.getDescription(false), violations);

            ErrorResponse errorResponse = ErrorResponse.builder()
                    .type("https://api.onlineshop.com/errors/invalid-authorization")
                    .title("Unauthorized")
                    .status(HttpStatus.UNAUTHORIZED.value())
                    .detail("Authorization header is required.")
                    .instance(request.getDescription(false).replace("uri=", ""))
                    .build();

            return new ResponseEntity<>(errorResponse, HttpStatus.UNAUTHORIZED);
        }

        logger.warn("Constraint violation on {}: {}", request.getDescription(false), violations);

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/validation-failed")
                .title("Bad Request")
                .status(HttpStatus.BAD_REQUEST.value())
                .detail("Validation failed: " + violations)
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.BAD_REQUEST);
    }

    @ExceptionHandler(NoHandlerFoundException.class)
    public ResponseEntity<ErrorResponse> handleNoHandlerFoundException(
            NoHandlerFoundException ex,
            WebRequest request) {
        logger.warn("Requested endpoint not found: {}", request.getDescription(false));

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/not-found")
                .title("Not Found")
                .status(HttpStatus.NOT_FOUND.value())
                .detail("The requested endpoint does not exist.")
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.NOT_FOUND);
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ErrorResponse> handleMethodNotSupportedException(
            HttpRequestMethodNotSupportedException ex,
            WebRequest request) {
        logger.warn("HTTP method not supported on {}: {}", request.getDescription(false), ex.getMethod());

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/method-not-allowed")
                .title("Method Not Allowed")
                .status(HttpStatus.METHOD_NOT_ALLOWED.value())
                .detail("The HTTP method is not supported for this endpoint.")
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.METHOD_NOT_ALLOWED);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ErrorResponse> handleGenericException(
            Exception ex,
            WebRequest request) {
        logger.warn("Unexpected error occurred on hitting {}", request.getDescription(false), ex);

        ErrorResponse errorResponse = ErrorResponse.builder()
                .type("https://api.onlineshop.com/errors/internal-server-error")
                .title("Internal Server Error")
                .status(HttpStatus.INTERNAL_SERVER_ERROR.value())
                .detail("An unexpected error occurred. Please try again later.")
                .instance(request.getDescription(false).replace("uri=", ""))
                .build();

        return new ResponseEntity<>(errorResponse, HttpStatus.INTERNAL_SERVER_ERROR);
    }
}
